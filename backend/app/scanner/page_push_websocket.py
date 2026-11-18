"""Durable, multi-worker browser-facing scanner page WebSocket.

Only public scanner read models belong on this channel. Private account data
uses the authenticated Box Paper channel. One DB journal tailer per worker
fans out to local sockets; browser commands are limited to subscription and
resynchronization in this transport.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import Column, Float, Integer, JSON, MetaData, String, Table, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import engine

router = APIRouter(tags=["page-push"])
PAGE_CHANNELS = frozenset({
    "cash-future",
    "box-spread",
    "calendar-spread",
    "synthetic-arbitrage",
    "iv-skew-flip",
    "credit-butterfly-condor-weekly",
    "credit-butterfly-condor-monthly",
    "weekly-swing",
})
QUEUE_SIZE = 32
HEARTBEAT_SECONDS = 20.0
DISPATCH_POLL_SECONDS = 0.25
_log = logging.getLogger(__name__)
metadata = MetaData()
events = Table(
    "algo_scanner_page_ws_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("page", String(64), nullable=False, index=True),
    Column("page_sequence", Integer, nullable=False),
    Column("event", String(24), nullable=False),
    Column("data", JSON, nullable=False),
    Column("created_at", Float, nullable=False, index=True),
)
sequences = Table(
    "algo_scanner_page_ws_sequences", metadata,
    Column("page", String(64), primary_key=True),
    Column("sequence", Integer, nullable=False, default=0),
)
metadata.create_all(bind=engine)

_snapshot_providers: dict[str, Callable[[], dict[str, Any]]] = {}
_subscribers: set[tuple[asyncio.Queue, frozenset[str]]] = set()
_dispatch_task: asyncio.Task | None = None
_dispatch_loop: asyncio.AbstractEventLoop | None = None
_dispatch_ready: asyncio.Event | None = None
_subscriber_lock = threading.RLock()


def _seed_sequences() -> None:
    for page in PAGE_CHANNELS:
        try:
            with engine.begin() as connection:
                current = connection.execute(
                    select(sequences.c.page).where(sequences.c.page == page)
                ).first()
                if current is None:
                    connection.execute(insert(sequences).values(page=page, sequence=0))
        except IntegrityError:
            # Another worker may have seeded the same page concurrently.
            pass


_seed_sequences()


def configure_snapshot_provider(page: str, provider: Callable[[], dict[str, Any]] | None) -> None:
    if page not in PAGE_CHANNELS:
        raise ValueError("unknown page channel")
    if provider is None:
        _snapshot_providers.pop(page, None)
    else:
        _snapshot_providers[page] = provider


def _latest_event_id() -> int:
    with engine.connect() as connection:
        value = connection.execute(select(func.max(events.c.id))).scalar_one_or_none()
    return int(value or 0)


def _latest_page_sequence(page: str) -> int:
    with engine.connect() as connection:
        value = connection.execute(
            select(sequences.c.sequence).where(sequences.c.page == page)
        ).scalar_one_or_none()
    return int(value or 0)


def _events_after(cursor: int, limit: int = 100) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(events.c.id, events.c.page, events.c.page_sequence, events.c.event, events.c.data)
            .where(events.c.id > cursor)
            .order_by(events.c.id.asc()).limit(limit + 1)
        ).all()
    return [
        {"id": int(row[0]), "page": str(row[1]), "sequence": int(row[2]),
         "event": str(row[3]), "data": row[4] if isinstance(row[4], dict) else {}}
        for row in rows
    ]


def _frame(page: str, event: str, data: dict[str, Any] | None = None, sequence: int | None = None) -> dict[str, Any]:
    return {
        "type": "page_update", "page": page, "event": event,
        "sequence": int(sequence if sequence is not None else _latest_page_sequence(page)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": data if isinstance(data, dict) else {},
    }


def _snapshot_frame(page: str, data: dict[str, Any] | None = None, sequence: int | None = None) -> dict[str, Any]:
    return _frame(page, "SNAPSHOT", data, sequence)


def _snapshot(page: str) -> dict[str, Any]:
    provider = _snapshot_providers.get(page)
    try:
        result = provider() if callable(provider) else {}
        return result if isinstance(result, dict) else {}
    except Exception:
        _log.exception("Scanner page snapshot failed for %s", page)
        return {"snapshot_status": "UNAVAILABLE"}


def publish_page_update(page: str, event: str, data: dict[str, Any] | None = None) -> None:
    """Durably publish a public scanner update for every backend worker."""
    if page not in PAGE_CHANNELS or event not in {"PATCH", "REMOVE", "SNAPSHOT"}:
        return
    safe_data = data if isinstance(data, dict) else {}
    try:
        with engine.begin() as connection:
            row = connection.execute(
                update(sequences)
                .where(sequences.c.page == page)
                .values(sequence=sequences.c.sequence + 1)
                .returning(sequences.c.sequence)
            ).first()
            if row is None:
                connection.execute(insert(sequences).values(page=page, sequence=0))
                row = connection.execute(
                    update(sequences)
                    .where(sequences.c.page == page)
                    .values(sequence=sequences.c.sequence + 1)
                    .returning(sequences.c.sequence)
                ).first()
            next_sequence = int(row[0])
            connection.execute(insert(events).values(
                page=page, page_sequence=next_sequence, event=event,
                data=safe_data, created_at=datetime.now(timezone.utc).timestamp(),
            ))
            cutoff = connection.execute(
                select(events.c.id).where(events.c.page == page)
                .order_by(events.c.id.desc()).offset(10000).limit(1)
            ).scalar_one_or_none()
            if cutoff is not None:
                connection.execute(delete(events).where(events.c.page == page, events.c.id < int(cutoff)))
    except Exception:
        _log.exception("Could not persist scanner page update for %s", page)


async def _journal_dispatch_loop() -> None:
    global _dispatch_ready
    try:
        cursor = await asyncio.to_thread(_latest_event_id)
    except Exception:
        cursor = 0
        _log.exception("Scanner page WebSocket journal baseline failed")
    if _dispatch_ready is not None:
        _dispatch_ready.set()
    while _subscribers:
        try:
            rows = await asyncio.to_thread(_events_after, cursor)
            if not rows:
                await asyncio.sleep(DISPATCH_POLL_SECONDS)
                continue
            for row in rows:
                cursor = row["id"]
                frame = {
                    "type": "page_update", "page": row["page"],
                    "event": row["event"], "sequence": row["sequence"],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "data": row["data"],
                }
                for queue, channels in tuple(_subscribers):
                    if row["page"] not in channels:
                        continue
                    if queue.full():
                        while not queue.empty():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        for subscribed_page in channels:
                            latest_sequence = (
                                row["sequence"] if subscribed_page == row["page"]
                                else await asyncio.to_thread(_latest_page_sequence, subscribed_page)
                            )
                            queue.put_nowait({
                                "type": "page_update", "page": subscribed_page,
                                "event": "SNAPSHOT_REQUIRED", "sequence": latest_sequence,
                                "generated_at": datetime.now(timezone.utc).isoformat(), "data": {},
                            })
                    else:
                        queue.put_nowait(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("Scanner page WebSocket journal poll failed")
            await asyncio.sleep(DISPATCH_POLL_SECONDS)


async def _subscribe_local() -> asyncio.Queue:
    global _dispatch_task, _dispatch_loop, _dispatch_ready
    loop = asyncio.get_running_loop()
    if _dispatch_task is not None and _dispatch_loop is not loop:
        _dispatch_task.cancel()
        _dispatch_task = None
        _subscribers.clear()
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers.add((queue, frozenset()))
    _dispatch_loop = loop
    if _dispatch_task is None or _dispatch_task.done():
        _dispatch_ready = asyncio.Event()
        _dispatch_task = loop.create_task(_journal_dispatch_loop())
    ready = _dispatch_ready
    if ready is not None:
        await ready.wait()
    return queue


def _set_channels(queue: asyncio.Queue, channels: frozenset[str]) -> None:
    for entry in tuple(_subscribers):
        if entry[0] is queue:
            _subscribers.discard(entry)
    _subscribers.add((queue, channels))


def _unsubscribe_local(queue: asyncio.Queue) -> None:
    global _dispatch_task, _dispatch_loop, _dispatch_ready
    for entry in tuple(_subscribers):
        if entry[0] is queue:
            _subscribers.discard(entry)
    if not _subscribers and _dispatch_task is not None:
        _dispatch_task.cancel()
        _dispatch_task = None
        _dispatch_loop = None
        _dispatch_ready = None


async def _restart_event_waiter(queue: asyncio.Queue, event_task: asyncio.Task, page_to_reset: str) -> asyncio.Task:
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
            break
    return asyncio.create_task(queue.get())


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = str(websocket.headers.get("origin") or "").strip()
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host in {"localhost", "127.0.0.1"}
        or host.endswith(".vercel.app")
        or host == "bhaskarprasad386.workers.dev"
        or host.endswith(".bhaskarprasad386.workers.dev")
    )


@router.websocket("/ws/page-updates")
async def page_updates(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = await _subscribe_local()
    channels: frozenset[str] = frozenset()
    client_sequences: dict[str, int] = {}
    try:
        await websocket.send_json({
            "type": "connected", "server_time": datetime.now(timezone.utc).isoformat(),
            "available_pages": sorted(PAGE_CHANNELS),
        })
        receive_task = asyncio.create_task(websocket.receive_text())
        event_task = asyncio.create_task(queue.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {receive_task, event_task}, timeout=HEARTBEAT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    await websocket.send_json({
                        "type": "heartbeat", "server_time": datetime.now(timezone.utc).isoformat(),
                        "subscribed_pages": sorted(channels),
                    })
                    continue
                if event_task in done:
                    frame = event_task.result()
                    page = str(frame.get("page") or "") if isinstance(frame, dict) else ""
                    if page in channels:
                        sequence = int(frame.get("sequence") or 0)
                        last = client_sequences.get(page, 0)
                        if frame.get("event") == "SNAPSHOT_REQUIRED" or (last and sequence != last + 1):
                            sequence = await asyncio.to_thread(_latest_page_sequence, page)
                            await websocket.send_json(_snapshot_frame(page, _snapshot(page), sequence))
                            client_sequences[page] = sequence
                        elif sequence > last:
                            await websocket.send_json(frame)
                            client_sequences[page] = sequence
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
                        _set_channels(queue, channels)
                        event_task = await _restart_event_waiter(queue, event_task, page)
                    elif message["action"] == "unsubscribe":
                        channels = frozenset(item for item in channels if item != page)
                        _set_channels(queue, channels)
                        client_sequences.pop(page, None)
                        await websocket.send_json({"type": "unsubscribed", "page": page})
                        continue
                    elif page not in channels:
                        await websocket.send_json({"type": "error", "code": "NOT_SUBSCRIBED"})
                        continue
                    else:
                        event_task = await _restart_event_waiter(queue, event_task, page)
                    sequence = await asyncio.to_thread(_latest_page_sequence, page)
                    await websocket.send_json(_snapshot_frame(page, _snapshot(page), sequence))
                    client_sequences[page] = sequence
        finally:
            receive_task.cancel()
            event_task.cancel()
            await asyncio.gather(receive_task, event_task, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        _unsubscribe_local(queue)
