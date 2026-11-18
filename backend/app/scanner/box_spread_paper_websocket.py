"""Authenticated owner-scoped Box Paper Trading WebSocket transport.

Tickets are opaque, short-lived, single-use values stored hashed in the app DB.
Fan-out is process-local; every connection/recovery begins from a fresh DB snapshot.
Private data never enters the public page-push channel.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import threading
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import Column, Float, MetaData, String, Table, delete, insert
from app.core.database import engine

router = APIRouter(tags=["box-spread-paper-private-ws"])
CHANNEL = "box-spread-paper-private"
TICKET_TTL_SECONDS = 30
MAX_MESSAGE_BYTES = 2048
QUEUE_SIZE = 16
HEARTBEAT_SECONDS = 20.0
metadata = MetaData()
tickets = Table(
    "box_spread_paper_ws_tickets", metadata,
    Column("ticket_hash", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False, index=True),
    Column("channel", String(64), nullable=False),
    Column("expires_at", Float, nullable=False, index=True),
)
metadata.create_all(bind=engine)

_snapshot_provider: Callable[[], dict[str, Any]] | None = None
_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_sequence = 0
_lock = threading.Lock()


def configure_snapshot_provider(provider: Callable[[], dict[str, Any]] | None) -> None:
    global _snapshot_provider
    _snapshot_provider = provider


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def issue_ticket(owner_id: str) -> str:
    raw = secrets.token_urlsafe(32)
    now = _now()
    with engine.begin() as connection:
        connection.execute(delete(tickets).where(tickets.c.expires_at <= now))
        connection.execute(insert(tickets).values(
            ticket_hash=_digest(raw), owner_id=owner_id,
            channel=CHANNEL, expires_at=now + TICKET_TTL_SECONDS,
        ))
    return raw


def consume_ticket(raw: str) -> str | None:
    if not isinstance(raw, str) or not 20 <= len(raw) <= 256:
        return None
    now = _now()
    # DELETE .. RETURNING is one atomic consume operation on supported SQLite
    # and PostgreSQL versions; a concurrent replay can win at most once.
    with engine.begin() as connection:
        row = connection.execute(
            delete(tickets).where(
                tickets.c.ticket_hash == _digest(raw),
                tickets.c.channel == CHANNEL,
                tickets.c.expires_at > now,
            ).returning(tickets.c.owner_id)
        ).first()
    return str(row[0]) if row else None


def _paper_claims(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    from app.scanner.box_spread_paper_routes import require_paper_access
    return require_paper_access(authorization)


@router.post("/api/v1/scanner/box-spread-paper/ws-ticket")
def create_paper_ws_ticket(claims: dict[str, Any] = Depends(_paper_claims)):
    if claims.get("sub") != "box-spread-paper-owner" or claims.get("scope") != "box-spread-paper":
        raise HTTPException(status_code=401, detail="Invalid paper-trading session")
    return {
        "ticket": issue_ticket("box-spread-paper-owner"),
        "expires_in": TICKET_TTL_SECONDS,
        "channel": CHANNEL,
    }


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = str(websocket.headers.get("origin") or "").strip()
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"https", "http"} and (
        host in {"localhost", "127.0.0.1"}
        or host.endswith(".vercel.app")
        or host == "bhaskarprasad386.workers.dev"
        or host.endswith(".bhaskarprasad386.workers.dev")
    )


def _snapshot_frame(event_type: str = "SNAPSHOT", account_key: str = "default") -> dict[str, Any]:
    global _sequence
    with _lock:
        if _sequence < 1:
            _sequence = 1
        sequence = _sequence
    provider = _snapshot_provider
    try:
        payload = provider(account_key) if callable(provider) else {}
    except Exception:
        payload = {"snapshot_status": "UNAVAILABLE"}
    return {
        "type": "paper_update", "channel": CHANNEL, "schema_version": 1,
        "sequence": sequence, "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload if isinstance(payload, dict) else {"snapshot_status": "INVALID"},
    }


def publish_paper_update(event_type: str, _payload: dict[str, Any] | None = None) -> None:
    """Notify private subscribers after a successful route/DB operation."""
    loop = _loop
    if loop is None or not loop.is_running():
        return

    def deliver() -> None:
        global _sequence
        with _lock:
            _sequence += 1
            sequence = _sequence
        # Queue only a bounded invalidation marker. The socket handler builds
        # the current snapshot from committed DB state when it sends the event.
        marker = {
            "type": "paper_invalidation", "sequence": sequence,
            "event_id": str(uuid.uuid4()), "event_type": event_type,
        }
        for queue in tuple(_subscribers):
            if queue.full():
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                marker = {**marker, "event_type": "SNAPSHOT_REQUIRED"}
            try:
                queue.put_nowait(marker)
            except asyncio.QueueFull:
                pass

    loop.call_soon_threadsafe(deliver)


def publish_after_success(event_type: str):
    """Route decorator: publish only after a successful handler return."""
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def async_wrapper(*args, **kwargs):
                result = await function(*args, **kwargs)
                publish_paper_update(event_type)
                return result
            return async_wrapper
        @wraps(function)
        def sync_wrapper(*args, **kwargs):
            result = function(*args, **kwargs)
            publish_paper_update(event_type)
            return result
        return sync_wrapper
    return decorate


@router.websocket("/ws/box-spread-paper")
async def box_spread_paper_private_updates(websocket: WebSocket) -> None:
    global _loop
    if not _origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            await websocket.close(code=1009)
            return
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            await websocket.close(code=1008)
            return
        ticket = message.get("ticket") if isinstance(message, dict) else None
        owner = consume_ticket(ticket)
        account_key = str(message.get("account_key") or "default") if isinstance(message, dict) else "default"
        if not account_key or len(account_key) > 128 or any(ord(ch) < 32 for ch in account_key):
            await websocket.close(code=1008)
            return
        if owner != "box-spread-paper-owner":
            await websocket.close(code=1008)
            return

        _subscribers.add(queue)
        await websocket.send_json(await asyncio.to_thread(_snapshot_frame, "SNAPSHOT", account_key))
        while True:
            receive_task = asyncio.create_task(websocket.receive_text())
            event_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                {receive_task, event_task}, timeout=HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                await websocket.send_json({
                    "type": "heartbeat", "channel": CHANNEL,
                    "server_time": datetime.now(timezone.utc).isoformat(),
                })
                continue
            if receive_task in done:
                incoming = receive_task.result()
                if len(incoming.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await websocket.close(code=1009)
                    return
                try:
                    command = json.loads(incoming)
                except (TypeError, ValueError):
                    await websocket.send_json({"type": "error", "code": "INVALID_JSON"})
                    continue
                if not isinstance(command, dict) or command.get("action") != "resync":
                    await websocket.send_json({"type": "error", "code": "INVALID_ACTION"})
                    continue
                await websocket.send_json(await asyncio.to_thread(_snapshot_frame, "SNAPSHOT", account_key))
            if event_task in done:
                marker = event_task.result()
                event_type = marker.get("event_type", "UPDATE") if isinstance(marker, dict) else "UPDATE"
                await websocket.send_json(await asyncio.to_thread(_snapshot_frame, "SNAPSHOT" if event_type == "SNAPSHOT_REQUIRED" else event_type, account_key))
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        _subscribers.discard(queue)
        if not _subscribers:
            _loop = None


__all__ = [
    "router", "configure_snapshot_provider", "publish_paper_update",
    "publish_after_success", "issue_ticket", "consume_ticket", "CHANNEL",
]
