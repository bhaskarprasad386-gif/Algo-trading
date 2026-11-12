"""Persistent alert event recording and restart-safe per-signal cooldowns."""
from __future__ import annotations

import json
from datetime import datetime, time as clock_time
from zoneinfo import ZoneInfo
from threading import RLock
from typing import Any

from app.models.iv_early_entry_alert import IVEarlyEntryAlertEvent, IVEarlyEntryCooldown
from app.models.iv_early_entry_notification import IVEarlyEntryNotificationOutbox
from .models import AlertCandidate, AlertState


class AlertCooldownStore:
    def __init__(self, session_factory: Any, *, cooldown_seconds: int = 600) -> None:
        if cooldown_seconds < 1:
            raise ValueError("cooldown_seconds must be positive")
        self.session_factory = session_factory
        self.cooldown_ns = cooldown_seconds * 1_000_000_000
        self._lock = RLock()

    @staticmethod
    def key(candidate: AlertCandidate) -> str:
        return "|".join((
            candidate.strategy.upper(), candidate.underlying.upper(),
            candidate.expiry, f"{candidate.strike:g}",
            candidate.direction.upper(),
        ))

    def record(self, candidate: AlertCandidate) -> dict[str, Any]:
        """Persist event and cooldown atomically; return accepted=False during cooldown."""
        if candidate.state != AlertState.EARLY_ENTRY:
            raise ValueError("only EARLY_ENTRY candidates can consume cooldown")
        key = self.key(candidate)
        session = self.session_factory()
        try:
            # Serialize local evaluator threads; DB transaction makes event and
            # cooldown update commit or roll back together.
            with self._lock:
                with session.begin():
                    state = session.get(IVEarlyEntryCooldown, key)
                    if state is not None and candidate.observed_at_ns - state.last_alert_ns < self.cooldown_ns:
                        return {
                            "accepted": False, "reason": "COOLDOWN_ACTIVE",
                            "cooldown_key": key,
                            "remaining_ns": max(0, self.cooldown_ns - (candidate.observed_at_ns - state.last_alert_ns)),
                        }
                    if state is None:
                        state = IVEarlyEntryCooldown(
                            cooldown_key=key, last_alert_ns=candidate.observed_at_ns,
                            expiry=candidate.expiry, strategy=candidate.strategy,
                        )
                        session.add(state)
                    else:
                        state.last_alert_ns = candidate.observed_at_ns
                        state.expiry = candidate.expiry
                        state.strategy = candidate.strategy
                    event = IVEarlyEntryAlertEvent(
                        strategy=candidate.strategy, underlying=candidate.underlying,
                        expiry=candidate.expiry, strike=candidate.strike,
                        direction=candidate.direction, option_type=candidate.option_type,
                        state=candidate.state.value, observed_at_ns=candidate.observed_at_ns,
                        spot=candidate.spot, premium=candidate.premium,
                        iv_change_points=candidate.iv_change_points,
                        volume_ratio=candidate.volume_ratio, reason=candidate.reason,
                        metadata_json=json.dumps(candidate.metadata, sort_keys=True, separators=(",", ":"), default=str),
                        cooldown_key=key, delivery_status="PENDING",
                    )
                    session.add(event)
                    session.flush()
                    event_id = event.id
                    notification_payload = {
                        "event_id": event_id, "strategy": candidate.strategy,
                        "underlying": candidate.underlying, "expiry": candidate.expiry,
                        "strike": candidate.strike, "direction": candidate.direction,
                        "option_type": candidate.option_type, "state": candidate.state.value,
                        "spot": candidate.spot, "premium": candidate.premium,
                        "iv_change_points": candidate.iv_change_points,
                        "volume_ratio": candidate.volume_ratio, "reason": candidate.reason,
                        "observed_at_ns": candidate.observed_at_ns,
                    }
                    session.add(IVEarlyEntryNotificationOutbox(
                        event_id=event_id, channel="TELEGRAM", status="PENDING",
                        payload_json=json.dumps(notification_payload, sort_keys=True, separators=(",", ":"), default=str),
                        attempts=0, next_attempt_ns=candidate.observed_at_ns,
                        lease_until_ns=None, last_error="",
                    ))
                return {"accepted": True, "event_id": event_id, "cooldown_key": key}
        finally:
            session.close()

    def record_conflict(self, candidate: AlertCandidate) -> dict[str, Any]:
        """Persist an opposing-signal candidate for manual review without cooldown."""
        if candidate.state not in {AlertState.EARLY_ENTRY, AlertState.CONFLICT}:
            raise ValueError("conflict records require an entry candidate")
        key = self.key(candidate)
        session = self.session_factory()
        try:
            row = IVEarlyEntryAlertEvent(
                strategy=candidate.strategy, underlying=candidate.underlying,
                expiry=candidate.expiry, strike=candidate.strike,
                direction=candidate.direction, option_type=candidate.option_type,
                state=AlertState.CONFLICT.value, observed_at_ns=candidate.observed_at_ns,
                spot=candidate.spot, premium=candidate.premium,
                iv_change_points=candidate.iv_change_points,
                volume_ratio=candidate.volume_ratio, reason=candidate.reason,
                metadata_json=json.dumps(candidate.metadata, sort_keys=True, separators=(",", ":"), default=str),
                cooldown_key=key, delivery_status="SKIPPED",
            )
            session.add(row)
            session.commit()
            return {"accepted": True, "event_id": row.id, "state": "CONFLICT"}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def transition(self, event_id: int, state: AlertState, *, reason: str) -> bool:
        """Idempotently update an alert lifecycle state without consuming cooldown."""
        if state not in {AlertState.INVALIDATED, AlertState.EXPIRED, AlertState.CONFLICT}:
            raise ValueError("unsupported lifecycle transition")
        session = self.session_factory()
        try:
            row = session.get(IVEarlyEntryAlertEvent, int(event_id))
            if row is None or row.state != AlertState.EARLY_ENTRY.value:
                return False
            row.state = state.value
            row.reason = str(reason)[:2000]
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def expire_session_alerts(self, *, now: datetime) -> int:
        """Expire active alerts after session close or after their option expiry."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        local = now.astimezone(ZoneInfo("Asia/Kolkata"))
        session = self.session_factory()
        changed = 0
        try:
            rows = (session.query(IVEarlyEntryAlertEvent)
                    .filter(IVEarlyEntryAlertEvent.state == AlertState.EARLY_ENTRY.value)
                    .all())
            for row in rows:
                try:
                    expiry_day = datetime.strptime(row.expiry[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if expiry_day < local.date():
                    row.state = AlertState.EXPIRED.value
                    row.reason = "OPTION_EXPIRY_PASSED"
                    changed += 1
                elif local.time() > clock_time(15, 5):
                    row.state = AlertState.EXPIRED.value
                    row.reason = "TRADING_SESSION_CLOSED"
                    changed += 1
            if changed:
                session.commit()
            return changed
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_delivery(self, event_id: int, status: str) -> bool:
        normalized = str(status).upper()
        if normalized not in {"PENDING", "SENT", "FAILED", "SKIPPED", "WAITING_CONFIG"}:
            raise ValueError("unsupported delivery status")
        session = self.session_factory()
        try:
            row = session.get(IVEarlyEntryAlertEvent, int(event_id))
            if row is None:
                return False
            row.delivery_status = normalized
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def latest(self, *, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        session = self.session_factory()
        try:
            rows = (session.query(IVEarlyEntryAlertEvent)
                    .order_by(IVEarlyEntryAlertEvent.observed_at_ns.desc(),
                              IVEarlyEntryAlertEvent.id.desc())
                    .limit(limit).all())
            return [{
                "id": row.id, "strategy": row.strategy, "underlying": row.underlying,
                "expiry": row.expiry, "strike": row.strike, "direction": row.direction,
                "option_type": row.option_type, "state": row.state,
                "observed_at_ns": row.observed_at_ns, "spot": row.spot,
                "premium": row.premium, "iv_change_points": row.iv_change_points,
                "volume_ratio": row.volume_ratio, "reason": row.reason,
                "metadata": json.loads(row.metadata_json or "{}"),
                "delivery_status": row.delivery_status,
            } for row in rows]
        finally:
            session.close()


__all__ = ["AlertCooldownStore"]
