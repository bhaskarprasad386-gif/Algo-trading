from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.iv_early_entry_alert import IVEarlyEntryAlertEvent, IVEarlyEntryCooldown
from app.models.iv_early_entry_notification import IVEarlyEntryNotificationOutbox
from app.scanner.iv_skew_flip.alerts.notifications import AmbiguousDeliveryError, TelegramOutboxWorker
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



def test_oi_wall_alert_invalidates_only_when_live_spot_breaches_wall():
    store = make_store()
    base = candidate()
    wall_candidate = AlertCandidate(
        "OI_WALL_REJECTION", "NIFTY", base.expiry, 25000.0,
        "BULLISH", "CE", AlertState.EARLY_ENTRY, base.observed_at_ns,
        25008.0, 105.0, -0.12, None, "support defended",
        {"wall_strike": 25000.0, "wall_option_type": "PE"},
    )
    result = store.record(wall_candidate)
    assert store.invalidate_breached_oi_walls(
        spot=25001.0, observed_at_ns=base.observed_at_ns + 1,
    ) == 0
    assert store.invalidate_breached_oi_walls(
        spot=24999.0, observed_at_ns=base.observed_at_ns + 2,
    ) == 1
    latest = store.latest()[0]
    assert latest["id"] == result["event_id"]
    assert latest["state"] == "INVALIDATED"
    assert latest["reason"] == "OI_WALL_BREACHED_BY_LIVE_SPOT"


def test_notification_worker_marks_expired_in_flight_delivery_uncertain():
    store = make_store()
    result = store.record(candidate())
    session = store.session_factory()
    try:
        row = session.query(IVEarlyEntryNotificationOutbox).filter_by(
            event_id=result["event_id"]
        ).one()
        row.status = "IN_FLIGHT"
        row.attempts = 1
        row.lease_until_ns = candidate().observed_at_ns + 1_000_000_000
        row.lease_token = "abandoned-worker"
        session.commit()
    finally:
        session.close()

    worker = TelegramOutboxWorker(store.session_factory, lease_seconds=2)
    delivered = []
    recovered = worker.process_one(
        now_ns=candidate().observed_at_ns + 3_000_000_000,
        sender=lambda payload: delivered.append(payload),
        configured=True,
    )
    assert recovered["status"] == "UNCERTAIN"
    assert delivered == []
    session = store.session_factory()
    try:
        row = session.query(IVEarlyEntryNotificationOutbox).one()
        assert row.status == "UNCERTAIN"
        assert row.lease_token is None
        assert row.lease_until_ns is None
    finally:
        session.close()
    assert store.latest()[0]["delivery_status"] == "UNCERTAIN"



def test_notification_worker_reports_terminal_failure_at_attempt_limit():
    store = make_store()
    store.record(candidate())
    worker = TelegramOutboxWorker(
        store.session_factory, max_attempts=1, base_retry_seconds=1,
    )

    def fail(_payload):
        raise TimeoutError("permanent for this attempt")

    result = worker.process_one(
        now_ns=candidate().observed_at_ns,
        sender=fail,
        configured=True,
    )
    assert result["status"] == "FAILED"
    session = store.session_factory()
    try:
        row = session.query(IVEarlyEntryNotificationOutbox).one()
        assert row.status == "FAILED"
        assert row.lease_token is None
        assert row.lease_until_ns is None
    finally:
        session.close()
    assert store.latest()[0]["delivery_status"] == "FAILED"


def test_expired_worker_cannot_overwrite_newer_lease_owner():
    store = make_store()
    store.record(candidate())
    worker = TelegramOutboxWorker(store.session_factory)

    def sender(_payload):
        # Simulate another worker reclaiming the expired lease while this
        # sender is still running. The new token owns all future transitions.
        session = store.session_factory()
        try:
            row = session.query(IVEarlyEntryNotificationOutbox).one()
            row.lease_token = "new-owner-token"
            row.lease_until_ns = candidate().observed_at_ns + 90_000_000_000
            session.commit()
        finally:
            session.close()

    result = worker.process_one(
        now_ns=candidate().observed_at_ns,
        sender=sender,
        configured=True,
    )
    assert result["status"] == "LEASE_LOST"
    session = store.session_factory()
    try:
        row = session.query(IVEarlyEntryNotificationOutbox).one()
        assert row.status == "IN_FLIGHT"
        assert row.lease_token == "new-owner-token"
    finally:
        session.close()
    assert store.latest()[0]["delivery_status"] == "PENDING"



def test_cooldown_claim_is_atomic_across_independent_store_instances(tmp_path):
    database = tmp_path / "concurrent-alerts.sqlite"
    engine_a = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine_a, tables=[
        IVEarlyEntryAlertEvent.__table__, IVEarlyEntryCooldown.__table__,
        IVEarlyEntryNotificationOutbox.__table__,
    ])
    engine_b = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    factory_a = sessionmaker(bind=engine_a, expire_on_commit=False)
    factory_b = sessionmaker(bind=engine_b, expire_on_commit=False)
    stores = [AlertCooldownStore(factory_a), AlertCooldownStore(factory_b)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.record(candidate()), stores))

    assert sum(result["accepted"] is True for result in results) == 1
    assert sum(result.get("reason") == "COOLDOWN_ACTIVE" for result in results) == 1
    session = factory_a()
    try:
        assert session.query(IVEarlyEntryAlertEvent).count() == 1
        assert session.query(IVEarlyEntryCooldown).count() == 1
        assert session.query(IVEarlyEntryNotificationOutbox).count() == 1
    finally:
        session.close()
        engine_a.dispose()
        engine_b.dispose()


def test_notification_worker_skips_alert_invalidated_before_delivery():
    store = make_store()
    result = store.record(candidate())
    assert store.transition(
        result["event_id"], AlertState.INVALIDATED, reason="PRICE_STRUCTURE_BROKE",
    )
    worker = TelegramOutboxWorker(store.session_factory)
    delivered = []
    skipped = worker.process_one(
        now_ns=candidate().observed_at_ns + 1_000_000_000,
        sender=lambda payload: delivered.append(payload),
        configured=True,
    )
    assert skipped["status"] == "SKIPPED"
    assert delivered == []
    assert store.latest()[0]["delivery_status"] == "SKIPPED"


def test_waiting_config_clears_lease_and_defers_next_attempt():
    store = make_store()
    store.record(candidate())
    worker = TelegramOutboxWorker(store.session_factory, config_retry_seconds=60)
    now_ns = candidate().observed_at_ns
    waiting = worker.process_one(
        now_ns=now_ns, sender=None, configured=False,
    )
    assert waiting["status"] == "WAITING_CONFIG"
    session = store.session_factory()
    try:
        row = session.query(IVEarlyEntryNotificationOutbox).one()
        assert row.lease_until_ns is None
        assert row.lease_token is None
        assert row.next_attempt_ns == now_ns + 60_000_000_000
    finally:
        session.close()
    not_due = worker.process_one(
        now_ns=now_ns + 30_000_000_000,
        sender=None, configured=False,
    )
    assert not_due["processed"] is False
    assert not_due["reason"] == "NO_DUE_NOTIFICATION"



def test_cooldown_deduplicates_same_contract_across_strategies():
    store = make_store()
    first = store.record(candidate())
    base = candidate(1_800_000_001_000_000_000)
    other_strategy = AlertCandidate(
        "OI_WALL_REJECTION", base.underlying, base.expiry, base.strike,
        base.direction, base.option_type, base.state, base.observed_at_ns,
        base.spot, base.premium, -0.12, None, "same market move via OI wall",
    )
    second = store.record(other_strategy)
    assert first["accepted"] is True
    assert second["accepted"] is False
    assert second["reason"] == "COOLDOWN_ACTIVE"
    assert len(store.latest()) == 1



def test_invalidation_releases_only_its_own_cooldown_for_fresh_signal():
    store = make_store()
    first = store.record(candidate())
    assert first["accepted"] is True
    assert store.transition(
        first["event_id"], AlertState.INVALIDATED, reason="PRICE_STRUCTURE_BROKE",
    )
    fresh = candidate(1_800_000_001_000_000_000)
    second = store.record(fresh)
    assert second["accepted"] is True
    assert len(store.latest()) == 2


def test_late_invalidation_does_not_clear_newer_cooldown_claim():
    store = make_store()
    first = store.record(candidate())
    later = candidate(1_800_000_000_700_000_000)
    # Cooldown is 600s, so the newer claim cannot be recorded yet. Simulate
    # a newer claim at the DB layer to exercise the timestamp ownership guard.
    session = store.session_factory()
    try:
        cooldown = session.get(IVEarlyEntryCooldown, first["cooldown_key"])
        cooldown.last_alert_ns = later.observed_at_ns
        session.commit()
    finally:
        session.close()
    assert store.transition(
        first["event_id"], AlertState.INVALIDATED, reason="LATE_INVALIDATION",
    )
    session = store.session_factory()
    try:
        cooldown = session.get(IVEarlyEntryCooldown, first["cooldown_key"])
        assert cooldown is not None
        assert cooldown.last_alert_ns == later.observed_at_ns
    finally:
        session.close()



def test_distinct_strikes_can_each_emit_a_valid_alert_in_same_window():
    store = make_store()
    first = store.record(candidate())
    base = candidate()
    second_candidate = AlertCandidate(
        base.strategy, base.underlying, base.expiry, 25050.0,
        base.direction, base.option_type, base.state,
        base.observed_at_ns + 1_000_000_000, base.spot,
        base.premium, base.iv_change_points, base.volume_ratio,
        "independent adjacent-strike signal",
    )
    second = store.record(second_candidate)
    assert first["accepted"] is True
    assert second["accepted"] is True
    assert len(store.latest()) == 2



def test_telegram_outbox_delivers_same_cycle_alerts_in_persisted_fifo_order():
    store = make_store()
    first = store.record(candidate())
    base = candidate()
    second_candidate = AlertCandidate(
        base.strategy, base.underlying, base.expiry, 25050.0,
        base.direction, base.option_type, base.state,
        base.observed_at_ns, base.spot, base.premium,
        base.iv_change_points, base.volume_ratio, "second strike",
    )
    second = store.record(second_candidate)
    worker = TelegramOutboxWorker(store.session_factory)
    delivered_ids = []
    for _ in range(2):
        result = worker.process_one(
            now_ns=base.observed_at_ns,
            sender=lambda payload: delivered_ids.append(payload["event_id"]),
            configured=True,
        )
        assert result["status"] == "SENT"
    assert delivered_ids == [first["event_id"], second["event_id"]]



def test_notification_worker_skips_alert_expired_before_delivery():
    store = make_store()
    result = store.record(candidate())
    closed = datetime(2026, 11, 12, 15, 6, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert store.expire_session_alerts(now=closed) == 1

    # A fresh worker instance simulates process restart: the durable outbox
    # remains, but the alert lifecycle state must prevent stale delivery.
    restarted_worker = TelegramOutboxWorker(store.session_factory)
    delivered = []
    skipped = restarted_worker.process_one(
        now_ns=candidate().observed_at_ns + 1_000_000_000,
        sender=lambda payload: delivered.append(payload),
        configured=True,
    )
    assert skipped["status"] == "SKIPPED"
    assert delivered == []
    assert store.latest()[0]["state"] == "EXPIRED"
    assert store.latest()[0]["delivery_status"] == "SKIPPED"

    session = store.session_factory()
    try:
        row = session.query(IVEarlyEntryNotificationOutbox).one()
        assert row.status == "SKIPPED"
        assert row.lease_token is None
        assert row.lease_until_ns is None
    finally:
        session.close()


def test_pending_notification_survives_worker_restart_and_is_delivered():
    store = make_store()
    result = store.record(candidate())

    # The first worker/process stops before claiming the durable outbox row.
    restarted_worker = TelegramOutboxWorker(store.session_factory)
    delivered = []
    sent = restarted_worker.process_one(
        now_ns=candidate().observed_at_ns + 1_000_000_000,
        sender=lambda payload: delivered.append(payload),
        configured=True,
    )
    assert sent["status"] == "SENT"
    assert [payload["event_id"] for payload in delivered] == [result["event_id"]]
    assert store.latest()[0]["delivery_status"] == "SENT"



def test_ambiguous_telegram_transport_failure_is_not_retried():
    store = make_store()
    store.record(candidate())
    worker = TelegramOutboxWorker(store.session_factory, base_retry_seconds=1)
    calls = []

    def ambiguous_sender(_payload):
        calls.append("attempted")
        raise AmbiguousDeliveryError("remote acceptance cannot be ruled out")

    first = worker.process_one(
        now_ns=candidate().observed_at_ns,
        sender=ambiguous_sender,
        configured=True,
    )
    assert first["status"] == "UNCERTAIN"

    # Even after the ordinary retry interval, uncertain deliveries are terminal
    # for automation and must never be sent a second time.
    second = worker.process_one(
        now_ns=candidate().observed_at_ns + 120_000_000_000,
        sender=ambiguous_sender,
        configured=True,
    )
    assert second["processed"] is False
    assert calls == ["attempted"]
    assert store.latest()[0]["delivery_status"] == "UNCERTAIN"

    session = store.session_factory()
    try:
        row = session.query(IVEarlyEntryNotificationOutbox).one()
        assert row.status == "UNCERTAIN"
        assert row.lease_token is None
        assert row.lease_until_ns is None
    finally:
        session.close()



def test_notification_revalidates_lifecycle_after_claim_before_dispatch():
    store = make_store()
    result = store.record(candidate())
    base_factory = store.session_factory
    factory_calls = 0

    def racing_factory():
        nonlocal factory_calls
        factory_calls += 1
        # The worker's second DB session is its final pre-dispatch check.
        # Simulate a spot invalidation winning after lease claim but before
        # that authorization check.
        if factory_calls == 2:
            assert store.transition(
                result["event_id"], AlertState.INVALIDATED,
                reason="SPOT_WALL_BREACHED_DURING_QUEUE_HANDOFF",
            )
        return base_factory()

    worker = TelegramOutboxWorker(racing_factory)
    delivered = []
    outcome = worker.process_one(
        now_ns=candidate().observed_at_ns,
        sender=lambda payload: delivered.append(payload),
        configured=True,
    )
    assert outcome["status"] == "SKIPPED"
    assert delivered == []
    assert store.latest()[0]["state"] == "INVALIDATED"
    assert store.latest()[0]["delivery_status"] == "SKIPPED"
