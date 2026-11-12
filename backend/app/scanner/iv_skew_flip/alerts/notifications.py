"""Lease-based notification outbox worker; transport is injected and credentials stay external."""
from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy import or_

from app.models.iv_early_entry_alert import IVEarlyEntryAlertEvent
from app.models.iv_early_entry_notification import IVEarlyEntryNotificationOutbox


class TelegramOutboxWorker:
    def __init__(
        self, session_factory: Any, *, max_attempts: int = 6,
        lease_seconds: int = 45, base_retry_seconds: int = 5,
    ) -> None:
        if max_attempts < 1 or lease_seconds < 1 or base_retry_seconds < 1:
            raise ValueError("retry and lease settings must be positive")
        self.session_factory = session_factory
        self.max_attempts = max_attempts
        self.lease_ns = lease_seconds * 1_000_000_000
        self.base_retry_ns = base_retry_seconds * 1_000_000_000

    def process_one(
        self, *, now_ns: int, sender: Callable[[dict[str, Any]], Any] | None,
        configured: bool,
    ) -> dict[str, Any]:
        """Claim one due message. If config is absent, never attempt network delivery."""
        session = self.session_factory()
        claimed_id = None
        try:
            with session.begin():
                row = (session.query(IVEarlyEntryNotificationOutbox)
                       .filter(
                           IVEarlyEntryNotificationOutbox.status.in_(("PENDING", "RETRY", "WAITING_CONFIG", "IN_FLIGHT")),
                           IVEarlyEntryNotificationOutbox.next_attempt_ns <= now_ns,
                           or_(IVEarlyEntryNotificationOutbox.lease_until_ns.is_(None),
                               IVEarlyEntryNotificationOutbox.lease_until_ns <= now_ns),
                       )
                       .order_by(IVEarlyEntryNotificationOutbox.next_attempt_ns.asc(),
                                 IVEarlyEntryNotificationOutbox.id.asc())
                       .with_for_update(skip_locked=True).first())
                if row is None:
                    return {"processed": False, "reason": "NO_DUE_NOTIFICATION"}
                if not configured or sender is None:
                    row.status = "WAITING_CONFIG"
                    row.lease_until_ns = None
                    row.last_error = "TELEGRAM_CONFIGURATION_MISSING"
                    event = session.get(IVEarlyEntryAlertEvent, row.event_id)
                    if event is not None:
                        event.delivery_status = "WAITING_CONFIG"
                    return {"processed": True, "status": "WAITING_CONFIG", "outbox_id": row.id}
                row.status = "IN_FLIGHT"
                row.lease_until_ns = now_ns + self.lease_ns
                row.attempts += 1
                claimed_id = row.id
                payload = json.loads(row.payload_json)
        finally:
            session.close()

        try:
            sender(payload)
            final_status, error = "SENT", ""
        except Exception as exc:
            final_status = "FAILED" if payload is None else "RETRY"
            error = f"{type(exc).__name__}: {exc}"[:2000]

        session = self.session_factory()
        try:
            with session.begin():
                row = session.get(IVEarlyEntryNotificationOutbox, claimed_id)
                if row is None or row.status != "IN_FLIGHT":
                    return {"processed": True, "status": "LEASE_LOST", "outbox_id": claimed_id}
                row.status = final_status
                row.lease_until_ns = None
                row.last_error = error
                if final_status == "RETRY":
                    if row.attempts >= self.max_attempts:
                        row.status = "FAILED"
                    else:
                        delay = self.base_retry_ns * (2 ** min(row.attempts - 1, 8))
                        row.next_attempt_ns = now_ns + delay
                event = session.get(IVEarlyEntryAlertEvent, row.event_id)
                if event is not None:
                    event.delivery_status = "SENT" if row.status == "SENT" else row.status
            return {"processed": True, "status": final_status, "outbox_id": claimed_id}
        finally:
            session.close()


__all__ = ["TelegramOutboxWorker"]
