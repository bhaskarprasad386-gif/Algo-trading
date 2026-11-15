"""Shared browser-facing page update WebSocket.

This transport is deliberately downstream of existing scanner runtimes. It owns
no broker connections, subscriptions, polling loops, or strategy calculations.
Clients subscribe to page channels and receive sequenced snapshots/patches.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["page-push"])
PAGE_CHANNELS = frozenset({
    "box-spread",
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
                frame_to_send = _frame(page, "SNAPSHOT", snapshot if isinstance(snapshot, dict) else {})
            else:
                frame_to_send = frame
            try:
                queue.put_nowait(frame_to_send)
            except asyncio.QueueFull:
                pass

    loop.call_soon_threadsafe(deliver)


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = str(websocket.headers.get("origin") or "").strip()
    if not origin:
        return True  # native clients may omit Origin; same policy as existing WS
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        host == "localhost"
        or host == "127.0.0.1"
        or host.endswith(".vercel.app")
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
        while True:
            receive_task = asyncio.create_task(websocket.receive_text())
            event_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                {receive_task, event_task},
                timeout=HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if not done:
                await websocket.send_json({
                    "type": "heartbeat",
                    "server_time": datetime.now(timezone.utc).isoformat(),
                    "subscribed_pages": sorted(channels),
                })
                continue
            if event_task in done:
                await websocket.send_json(event_task.result())
            if receive_task in done:
                raw = receive_task.result()
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
                provider = _snapshot_providers.get(page)
                try:
                    snapshot = provider() if callable(provider) else {}
                except Exception:
                    snapshot = {}
                await websocket.send_json(_frame(page, "SNAPSHOT", snapshot if isinstance(snapshot, dict) else {}))
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.discard(entry)
        if not _subscribers:
            _loop = None
