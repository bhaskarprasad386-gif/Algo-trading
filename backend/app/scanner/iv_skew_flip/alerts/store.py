"""Persistent alert event recording and restart-safe per-signal cooldowns."""
from __future__ import annotations

import json
from datetime import datetime, time as clock_time
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.models.iv_early_entry_alert import IVEarlyEntryAlertEvent, IVEarlyEntryCooldown
from app.models.iv_early_entry_notification import IVEarlyEntryNotificationOutbox
from .models import AlertCandidate, AlertState


class AlertCooldownStore:
    def __init__(self, session_factory: Any, *, cooldown_seconds: int = 600) -> None:
        if cooldown_seconds < 1:
            raise ValueError("cooldown_seconds must be positive")
        self.session_factory = session_factory
        self.cooldown_ns = cooldown_seconds * 1_000_000_000

    @staticmethod
    def key(candidate: AlertCandidate) -> str:
        # A single market move can satisfy both Strategy A and Strategy B.
        # Share the cooldown across strategies for the same contract/direction
        # so the user receives one actionable alert, not duplicate messages.
        return "|".join((
            candidate.underlying.upper(), candidate.expiry,
            f"{candidate.strike:g}", candidate.direction.upper(),
        ))

    def record(self, candidate: AlertCandidate) -> dict[str, Any]:
        """Atomically claim the cooldown key and persist event plus outbox."""
        if candidate.state != AlertState.EARLY_ENTRY:
            raise ValueError("only EARLY_ENTRY candidates can consume cooldown")
        key = self.key(candidate)
        session = self.session_factory()
        try:
            try:
                with session.begin():
                    # Conditional UPDATE is the cross-process compare-and-set:
                    # the database rechecks the cooldown predicate after any
                    # competing writer commits. It also obtains SQLite's write
                    # lock before the absent-row insert path.
                    claimed = session.execute(
                        update(IVEarlyEntryCooldown)
                        .where(
                            IVEarlyEntryCooldown.cooldown_key == key,
                            IVEarlyEntryCooldown.last_alert_ns
                            <= candidate.observed_at_ns - self.cooldown_ns,
                        )
                        .values(
                            last_alert_ns=candidate.observed_at_ns,
                            expiry=candidate.expiry,
                            strategy=candidate.strategy,
                        )
                    )
                    if claimed.rowcount == 0:
                        state = session.get(IVEarlyEntryCooldown, key)
                        if state is not None:
                            elapsed = candidate.observed_at_ns - state.last_alert_ns
                            if elapsed < self.cooldown_ns:
                                return {
                                    "accepted": False, "reason": "COOLDOWN_ACTIVE",
                                    "cooldown_key": key,
                                    "remaining_ns": max(0, self.cooldown_ns - elapsed),
                                }
                        else:
                            session.add(IVEarlyEntryCooldown(
                                cooldown_key=key,
                                last_alert_ns=candidate.observed_at_ns,
                                expiry=candidate.expiry,
                                strategy=candidate.strategy,
                            ))

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
            except IntegrityError:
                # On databases where two first-time inserts can race, the
                # unique cooldown key elects one winner. Re-read after rollback.
                session.rollback()
                state = session.get(IVEarlyEntryCooldown, key)
                if state is not None:
                    elapsed = candidate.observed_at_ns - state.last_alert_ns
                    if elapsed < self.cooldown_ns:
                        return {
                            "accepted": False, "reason": "COOLDOWN_ACTIVE",
                            "cooldown_key": key,
                            "remaining_ns": max(0, self.cooldown_ns - elapsed),
                        }
                raise
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

    @staticmethod
    def _skip_queued_notifications(session: Any, event_id: int) -> int:
        """Cancel notifications not yet claimed; an in-flight HTTP send cannot be recalled."""
        result = session.execute(
            update(IVEarlyEntryNotificationOutbox)
            .where(
                IVEarlyEntryNotificationOutbox.event_id == int(event_id),
                IVEarlyEntryNotificationOutbox.status.in_(("PENDING", "RETRY", "WAITING_CONFIG")),
            )
            .values(
                status="SKIPPED", lease_until_ns=None, lease_token=None,
                last_error="ALERT_NO_LONGER_ACTIVE",
            )
        )
        return int(result.rowcount or 0)

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
            if self._skip_queued_notifications(session, row.id):
                row.delivery_status = "SKIPPED"
            if state == AlertState.INVALIDATED:
                # Release only this event's cooldown claim. The timestamp
                # predicate prevents a late lifecycle update from clearing a
                # newer alert's cooldown for the same signal key.
                session.query(IVEarlyEntryCooldown).filter(
                    IVEarlyEntryCooldown.cooldown_key == row.cooldown_key,
                    IVEarlyEntryCooldown.last_alert_ns == row.observed_at_ns,
                ).delete(synchronize_session=False)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def invalidate_breached_oi_walls(self, *, spot: float, observed_at_ns: int) -> int:
        """Invalidate OI-wall alerts only when spot breaches the recorded wall level."""
        if not isinstance(spot, (int, float)) or spot <= 0:
            return 0
        session = self.session_factory()
        changed = 0
        try:
            rows = (session.query(IVEarlyEntryAlertEvent)
                    .filter(
                        IVEarlyEntryAlertEvent.strategy == "OI_WALL_REJECTION",
                        IVEarlyEntryAlertEvent.state == AlertState.EARLY_ENTRY.value,
                    ).all())
            for row in rows:
                try:
                    metadata = json.loads(row.metadata_json or "{}")
                    wall = float(metadata["wall_strike"])
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
                breached = (
                    row.direction == "BULLISH" and float(spot) < wall
                ) or (
                    row.direction == "BEARISH" and float(spot) > wall
                )
                if breached and observed_at_ns >= row.observed_at_ns:
                    row.state = AlertState.INVALIDATED.value
                    row.reason = "OI_WALL_BREACHED_BY_LIVE_SPOT"
                    if self._skip_queued_notifications(session, row.id):
                        row.delivery_status = "SKIPPED"
                    session.query(IVEarlyEntryCooldown).filter(
                        IVEarlyEntryCooldown.cooldown_key == row.cooldown_key,
                        IVEarlyEntryCooldown.last_alert_ns == row.observed_at_ns,
                    ).delete(synchronize_session=False)
                    changed += 1
            if changed:
                session.commit()
            return changed
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _parse_expiry_day(value: Any):
        """Normalize the expiry formats accepted by the IV universe resolver."""
        raw = str(value or "").strip()
        for fmt in ("%Y-%m-%d", "%d%b%Y", "%d%b%y", "%d-%b-%Y", "%d-%b-%y"):
            try:
                return datetime.strptime(raw.upper(), fmt).date()
            except ValueError:
                continue
        return None

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
                expiry_day = self._parse_expiry_day(row.expiry)
                if expiry_day is None:
                    row.state = AlertState.EXPIRED.value
                    row.reason = "INVALID_EXPIRY_FORMAT"
                    if self._skip_queued_notifications(session, row.id):
                        row.delivery_status = "SKIPPED"
                    changed += 1
                elif expiry_day < local.date():
                    row.state = AlertState.EXPIRED.value
                    row.reason = "OPTION_EXPIRY_PASSED"
                    if self._skip_queued_notifications(session, row.id):
                        row.delivery_status = "SKIPPED"
                    changed += 1
                elif local.time() > clock_time(15, 5):
                    row.state = AlertState.EXPIRED.value
                    row.reason = "TRADING_SESSION_CLOSED"
                    if self._skip_queued_notifications(session, row.id):
                        row.delivery_status = "SKIPPED"
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
        if normalized not in {"PENDING", "SENT", "FAILED", "SKIPPED", "WAITING_CONFIG", "UNCERTAIN"}:
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
