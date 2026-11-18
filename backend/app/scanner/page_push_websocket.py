"""Shared browser-facing page update WebSocket.

This transport is deliberately downstream of existing scanner runtimes. It owns
no broker connections, subscriptions, polling loops, or strategy calculations.
Clients subscribe to page channels and receive sequenced snapshots/patches.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["page-push"])
PAGE_CHANNELS = frozenset({
    "box-spread",
    "errors",
    "iv-skew-flip",
    "credit-butterfly-condor-weekly",
    "credit-butterfly-condor-monthly",
})
QUEUE_SIZE = 8
HEARTBEAT_SECONDS = 20.0
_snapshot_providers: dict[str, Callable[[], dict[str, Any]]] = {}
_subscribers: set[tuple[asyncio.Queue, frozenset[str]]] = set()
_loop: asyncio.AbstractEventLoop | None = None
_sequences: dict[str, int] = {}


def configure_snapshot_provider(page: str, provider: Callable[[], dict[str, Any]] | None) -> None:
    """Register a read-only snapshot getter; providers must not start work."""
    if page not in PAGE_CHANNELS:
        raise ValueError("unknown page channel")
    if provider is None:
        _snapshot_providers.pop(page, None)
    else:
        _snapshot_providers[page] = provider


def _frame(page: str, event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    _sequences[page] = _sequences.get(page, 0) + 1
    return {
        "type": "page_update",
        "page": page,
        "event": event,
        "sequence": _sequences[page],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }


def _snapshot_frame(page: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """A resync snapshot aligns to the current sequence without advancing it."""
    if page not in _sequences:
        _sequences[page] = 1
    return {
        "type": "page_update",
        "page": page,
        "event": "SNAPSHOT",
        "sequence": _sequences[page],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }


def publish_page_update(page: str, event: str, data: dict[str, Any] | None = None) -> None:
    """Publish from any thread; slow clients are bounded and forced to resync."""
    if page not in PAGE_CHANNELS or event not in {"PATCH", "REMOVE", "SNAPSHOT"}:
        return
    loop = _loop
    if loop is None or not loop.is_running():
        return
    safe_data = data if isinstance(data, dict) else {}

    def deliver() -> None:
        frame = _frame(page, event, safe_data)
        for queue, channels in tuple(_subscribers):
            if page not in channels:
                continue
            if queue.full():
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                provider = _snapshot_providers.get(page)
                try:
                    snapshot = provider() if callable(provider) else {}
                except Exception:
                    snapshot = {}
                frame_to_send = {**frame, "event": "SNAPSHOT", "data": snapshot if isinstance(snapshot, dict) else {}}
            else:
                frame_to_send = frame
            try:
                queue.put_nowait(frame_to_send)
            except asyncio.QueueFull:
                pass

    loop.call_soon_threadsafe(deliver)


async def _restart_event_waiter(
    queue: asyncio.Queue,
    event_task: asyncio.Task,
    page_to_reset: str,
) -> asyncio.Task:
    """Discard pending events for one page while preserving other subscriptions."""
    preserved: list[dict[str, Any]] = []
    if event_task.done() and not event_task.cancelled():
        try:
            consumed = event_task.result()
            if isinstance(consumed, dict) and consumed.get("page") != page_to_reset:
                preserved.append(consumed)
        except Exception:
            pass
    else:
        event_task.cancel()
        await asyncio.gather(event_task, return_exceptions=True)

    while not queue.empty():
        try:
            queued = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(queued, dict) and queued.get("page") != page_to_reset:
            preserved.append(queued)
    for frame in preserved:
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            # A bounded queue cannot preserve an unbounded backlog. A later
            # sequence gap will make the client request a fresh page snapshot.
            break
    return asyncio.create_task(queue.get())


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = str(websocket.headers.get("origin") or "").strip()
    if not origin:
        return True  # native clients may omit Origin; same policy as existing WS
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    # Permit only this account's Cloudflare Workers subdomains. Do not
    # allow the shared workers.dev namespace or arbitrary tenant origins.
    return parsed.scheme in {"http", "https"} and (
        host in {"localhost", "127.0.0.1"}
        or host.endswith(".vercel.app")
        or host == "bhaskarprasad386.workers.dev"
        or host.endswith(".bhaskarprasad386.workers.dev")
    )


@router.websocket("/ws/page-updates")
async def page_updates(websocket: WebSocket) -> None:
    global _loop
    if not _origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    channels: frozenset[str] = frozenset()
    entry = (queue, channels)
    _subscribers.add(entry)
    try:
        await websocket.send_json({
            "type": "connected",
            "server_time": datetime.now(timezone.utc).isoformat(),
            "available_pages": sorted(PAGE_CHANNELS),
        })
        receive_task = asyncio.create_task(websocket.receive_text())
        event_task = asyncio.create_task(queue.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {receive_task, event_task},
                    timeout=HEARTBEAT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "server_time": datetime.now(timezone.utc).isoformat(),
                        "subscribed_pages": sorted(channels),
                    })
                    continue
                if event_task in done:
                    frame = event_task.result()
                    if isinstance(frame, dict) and frame.get("page") in channels:
                        await websocket.send_json(frame)
                    event_task = asyncio.create_task(queue.get())
                if receive_task in done:
                    raw = receive_task.result()
                    receive_task = asyncio.create_task(websocket.receive_text())
                    try:
                        message = json.loads(raw)
                    except (TypeError, ValueError):
                        await websocket.send_json({"type": "error", "code": "INVALID_JSON"})
                        continue
                    if not isinstance(message, dict) or message.get("action") not in {"subscribe", "unsubscribe", "resync"}:
                        await websocket.send_json({"type": "error", "code": "INVALID_ACTION"})
                        continue
                    page = str(message.get("page") or "")
                    if page not in PAGE_CHANNELS:
                        await websocket.send_json({"type": "error", "code": "UNKNOWN_PAGE"})
                        continue
                    if message["action"] == "subscribe":
                        channels = frozenset((*channels, page))
                        _subscribers.discard(entry)
                        entry = (queue, channels)
                        _subscribers.add(entry)
                        event_task = await _restart_event_waiter(queue, event_task, page)
                    elif message["action"] == "unsubscribe":
                        channels = frozenset(item for item in channels if item != page)
                        _subscribers.discard(entry)
                        entry = (queue, channels)
                        _subscribers.add(entry)
                        await websocket.send_json({"type": "unsubscribed", "page": page})
                        continue
                    elif page not in channels:
                        await websocket.send_json({"type": "error", "code": "NOT_SUBSCRIBED"})
                        continue
                    else:
                        # Cancel the outstanding queue.get as well as draining the
                        # queue. It may already have consumed a delta when the
                        # resync request arrives; allowing that task to complete
                        # after this snapshot would replay an older patch.
                        event_task = await _restart_event_waiter(queue, event_task, page)
                    provider = _snapshot_providers.get(page)
                    try:
                        snapshot = provider() if callable(provider) else {}
                    except Exception:
                        snapshot = {}
                    await websocket.send_json(_snapshot_frame(page, snapshot if isinstance(snapshot, dict) else {}))
        finally:
            receive_task.cancel()
            event_task.cancel()
            await asyncio.gather(receive_task, event_task, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.discard(entry)
        if not _subscribers:
            _loop = None
