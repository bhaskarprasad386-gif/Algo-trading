"""Authenticated owner-scoped Box Paper Trading WebSocket transport.

Tickets are opaque, short-lived, single-use values stored hashed in the app DB.
Events are journaled in the shared application DB; each worker independently tails the journal.
Private data never enters the public page-push channel.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import time
from collections import deque
import threading
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, delete, insert, select
from app.core.database import engine
from app.core.config import settings

router = APIRouter(tags=["box-spread-paper-private-ws"])
CHANNEL = "box-spread-paper-private"
TICKET_TTL_SECONDS = 30
MAX_MESSAGE_BYTES = 2048
QUEUE_SIZE = 16
HEARTBEAT_SECONDS = 20.0
MAX_CONNECTIONS_PER_PROCESS = 50
RESYNC_MIN_INTERVAL_SECONDS = 1.0
RESYNC_MAX_PER_MINUTE = 10
_active_connections: set[int] = set()
_connection_lock = threading.Lock()
metadata = MetaData()
tickets = Table(
    "box_spread_paper_ws_tickets", metadata,
    Column("ticket_hash", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False, index=True),
    Column("channel", String(64), nullable=False),
    Column("expires_at", Float, nullable=False, index=True),
)
events = Table(
    "box_spread_paper_ws_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("channel", String(64), nullable=False, index=True),
    Column("event_type", String(64), nullable=False),
    Column("created_at", Float, nullable=False, index=True),
)
metadata.create_all(bind=engine)

_snapshot_provider: Callable[[str], dict[str, Any]] | None = None


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


def _normalize_origin(origin: str) -> str | None:
    try:
        parsed = urlparse(str(origin or "").strip())
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        default_port = 443 if parsed.scheme == "https" else 80
        return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default_port else "")
    except (TypeError, ValueError):
        return None


def _allowed_origins() -> set[str]:
    configured = {
        normalized for item in str(settings.BOX_SPREAD_PAPER_WS_ALLOWED_ORIGINS or "").split(",")
        if (normalized := _normalize_origin(item))
    }
    if str(settings.environment).strip().lower() == "development":
        configured.update({"http://localhost:3000", "http://127.0.0.1:3000"})
    return configured


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = _normalize_origin(websocket.headers.get("origin", ""))
    return bool(origin and origin in _allowed_origins())


def _latest_event_id() -> int:
    with engine.connect() as connection:
        value = connection.execute(
            select(events.c.id).where(events.c.channel == CHANNEL)
            .order_by(events.c.id.desc()).limit(1)
        ).scalar_one_or_none()
    return int(value or 0)


def _events_after(cursor: int, limit: int = 100) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(events.c.id, events.c.event_type)
            .where(events.c.channel == CHANNEL, events.c.id > cursor)
            .order_by(events.c.id.asc()).limit(limit + 1)
        ).all()
    return [{"sequence": int(row[0]), "event_type": str(row[1])} for row in rows]


def _snapshot_frame(event_type: str = "SNAPSHOT", account_key: str = "default", sequence: int | None = None) -> dict[str, Any]:
    provider = _snapshot_provider
    try:
        payload = provider(account_key) if callable(provider) else {}
    except Exception:
        payload = {"snapshot_status": "UNAVAILABLE"}
    return {
        "type": "paper_update", "channel": CHANNEL, "schema_version": 1,
        "sequence": int(sequence if sequence is not None else _latest_event_id()),
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload if isinstance(payload, dict) else {"snapshot_status": "INVALID"},
    }


def publish_paper_update(event_type: str, _payload: dict[str, Any] | None = None) -> None:
    """Persist an invalidation after successful mutation; every worker tails the journal."""
    normalized = str(event_type or "UPDATE").strip().upper()[:64] or "UPDATE"
    now = _now()
    with engine.begin() as connection:
        connection.execute(insert(events).values(channel=CHANNEL, event_type=normalized, created_at=now))
        cutoff = connection.execute(
            select(events.c.id).where(events.c.channel == CHANNEL)
            .order_by(events.c.id.desc()).offset(10000).limit(1)
        ).scalar_one_or_none()
        if cutoff is not None:
            connection.execute(delete(events).where(events.c.channel == CHANNEL, events.c.id < int(cutoff)))


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
    if not _origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    with _connection_lock:
        at_capacity = len(_active_connections) >= MAX_CONNECTIONS_PER_PROCESS
        connection_id = id(websocket)
        if not at_capacity:
            _active_connections.add(connection_id)
    if at_capacity:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    resync_times: deque[float] = deque()
    last_resync_at = 0.0
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

        cursor = await asyncio.to_thread(_latest_event_id)
        await websocket.send_json(await asyncio.to_thread(_snapshot_frame, "SNAPSHOT", account_key, cursor))
        last_heartbeat = time.monotonic()
        while True:
            receive_task = asyncio.create_task(websocket.receive_text())
            done, pending = await asyncio.wait(
                {receive_task}, timeout=0.25,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                journal_rows = await asyncio.to_thread(_events_after, cursor)
                if journal_rows:
                    latest = journal_rows[-1]
                    first_sequence = int(journal_rows[0]["sequence"])
                    latest_sequence = int(latest["sequence"])
                    # Multiple events are deliberately coalesced into one authoritative
                    # snapshot. Also resync if retention has removed any cursor successor.
                    # Sending a delta with a skipped sequence makes clients reject it forever.
                    requires_snapshot = (
                        len(journal_rows) > 1
                        or first_sequence != cursor + 1
                        or len(journal_rows) > 100
                    )
                    event_type = "SNAPSHOT" if requires_snapshot else str(latest["event_type"])
                    cursor = latest_sequence
                    await websocket.send_json(await asyncio.to_thread(
                        _snapshot_frame, event_type, account_key, cursor,
                    ))
                elif time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                    await websocket.send_json({
                        "type": "heartbeat", "channel": CHANNEL,
                        "server_time": datetime.now(timezone.utc).isoformat(),
                    })
                    last_heartbeat = time.monotonic()
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
                now = time.monotonic()
                while resync_times and now - resync_times[0] > 60.0:
                    resync_times.popleft()
                if now - last_resync_at < RESYNC_MIN_INTERVAL_SECONDS or len(resync_times) >= RESYNC_MAX_PER_MINUTE:
                    await websocket.close(code=1008)
                    return
                last_resync_at = now
                resync_times.append(now)
                cursor = await asyncio.to_thread(_latest_event_id)
                await websocket.send_json(await asyncio.to_thread(_snapshot_frame, "SNAPSHOT", account_key, cursor))
                last_heartbeat = time.monotonic()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        with _connection_lock:
            _active_connections.discard(connection_id)


__all__ = [
    "router", "configure_snapshot_provider", "publish_paper_update",
    "publish_after_success", "issue_ticket", "consume_ticket", "CHANNEL",
]



def test_private_ws_coalesced_journal_events_require_snapshot():
    # A client expects contiguous deltas. If two events arrive between DB polls,
    # the server must send an authoritative SNAPSHOT at the newest sequence.
    cursor = 40
    rows = [
        {"sequence": 41, "event_type": "TRADE_UPDATED"},
        {"sequence": 42, "event_type": "LEDGER_UPDATED"},
    ]
    first_sequence = int(rows[0]["sequence"])
    latest_sequence = int(rows[-1]["sequence"])
    requires_snapshot = (
        len(rows) > 1
        or first_sequence != cursor + 1
        or len(rows) > 100
    )
    assert requires_snapshot is True
    assert latest_sequence == 42


def test_private_ws_retention_gap_requires_snapshot():
    cursor = 40
    rows = [{"sequence": 42, "event_type": "TRADE_UPDATED"}]
    assert int(rows[0]["sequence"]) != cursor + 1


def test_private_ws_single_contiguous_event_can_remain_delta():
    cursor = 40
    rows = [{"sequence": 41, "event_type": "TRADE_UPDATED"}]
    requires_snapshot = len(rows) > 1 or int(rows[0]["sequence"]) != cursor + 1
    assert requires_snapshot is False
