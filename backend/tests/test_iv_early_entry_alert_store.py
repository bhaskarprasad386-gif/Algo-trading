from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.iv_early_entry_alert import IVEarlyEntryAlertEvent, IVEarlyEntryCooldown
from app.models.iv_early_entry_notification import IVEarlyEntryNotificationOutbox
from app.scanner.iv_skew_flip.alerts.notifications import TelegramOutboxWorker
from app.scanner.iv_skew_flip.alerts.models import AlertCandidate, AlertState
from app.scanner.iv_skew_flip.alerts.store import AlertCooldownStore


def make_store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        IVEarlyEntryAlertEvent.__table__, IVEarlyEntryCooldown.__table__,
        IVEarlyEntryNotificationOutbox.__table__,
    ])
    return AlertCooldownStore(sessionmaker(bind=engine, expire_on_commit=False))


def candidate(ts=1_800_000_000_000_000_000):
    return AlertCandidate(
        "IV_VOLUME_MOMENTUM", "NIFTY", "2026-11-19", 25000.0,
        "BULLISH", "CE", AlertState.EARLY_ENTRY, ts, 25001.0,
        105.0, 0.25, 3.0, "test early entry",
    )


def test_cooldown_persists_and_blocks_duplicate_signal():
    store = make_store()
    first = store.record(candidate())
    second = store.record(candidate(first_ts := 1_800_000_000_500_000_000))
    assert first["accepted"] is True
    assert second["accepted"] is False
    assert second["reason"] == "COOLDOWN_ACTIVE"
    assert len(store.latest()) == 1


def test_cooldown_isolated_by_expiry_and_direction():
    store = make_store()
    assert store.record(candidate())["accepted"] is True
    other = candidate()
    other = AlertCandidate(
        other.strategy, other.underlying, "2026-11-26", other.strike,
        "BEARISH", "PE", other.state, other.observed_at_ns,
        other.spot, other.premium, other.iv_change_points,
        other.volume_ratio, other.reason,
    )
    assert store.record(other)["accepted"] is True


def test_delivery_status_is_persisted():
    store = make_store()
    result = store.record(candidate())
    assert store.mark_delivery(result["event_id"], "FAILED") is True
    assert store.latest()[0]["delivery_status"] == "FAILED"



def test_lifecycle_transition_is_idempotent_and_keeps_delivery_status():
    store = make_store()
    result = store.record(candidate())
    assert store.transition(result["event_id"], AlertState.INVALIDATED, reason="PRICE_STRUCTURE_BROKE")
    assert not store.transition(result["event_id"], AlertState.EXPIRED, reason="LATE_DUPLICATE")
    latest = store.latest()[0]
    assert latest["state"] == "INVALIDATED"
    assert latest["reason"] == "PRICE_STRUCTURE_BROKE"
    assert latest["delivery_status"] == "PENDING"


def test_active_alert_expires_after_session_close():
    store = make_store()
    result = store.record(candidate())
    closed = datetime(2026, 11, 12, 15, 6, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert store.expire_session_alerts(now=closed) == 1
    assert store.latest()[0]["state"] == "EXPIRED"
    assert store.latest()[0]["reason"] == "TRADING_SESSION_CLOSED"



def test_alert_and_telegram_outbox_are_created_atomically():
    store = make_store()
    result = store.record(candidate())
    engine = store.session_factory.kw.get("bind")
    with engine.connect() as conn:
        outbox_count = conn.execute(
            select(func.count()).select_from(IVEarlyEntryNotificationOutbox.__table__)
        ).scalar_one()
    assert result["accepted"] is True
    assert outbox_count == 1


def test_notification_waits_for_configuration_without_fake_sent_status():
    store = make_store()
    result = store.record(candidate())
    worker = TelegramOutboxWorker(store.session_factory)
    waiting = worker.process_one(
        now_ns=candidate().observed_at_ns, sender=None, configured=False,
    )
    assert waiting["status"] == "WAITING_CONFIG"
    assert store.latest()[0]["delivery_status"] == "WAITING_CONFIG"


def test_notification_worker_retries_and_then_marks_sent():
    store = make_store()
    result = store.record(candidate())
    worker = TelegramOutboxWorker(store.session_factory, base_retry_seconds=1)
    def fail(_payload):
        raise TimeoutError("temporary network failure")
    failed = worker.process_one(
        now_ns=candidate().observed_at_ns, sender=fail, configured=True,
    )
    assert failed["status"] == "RETRY"
    sent = []
    retried = worker.process_one(
        now_ns=candidate().observed_at_ns + 6_000_000_000,
        sender=lambda payload: sent.append(payload), configured=True,
    )
    assert retried["status"] == "SENT"
    assert len(sent) == 1
    assert sent[0]["event_id"] == result["event_id"]
    assert store.latest()[0]["delivery_status"] == "SENT"
