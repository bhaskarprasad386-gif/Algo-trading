from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.iv_early_entry_alert import IVEarlyEntryAlertEvent, IVEarlyEntryCooldown
from app.scanner.iv_skew_flip.alerts.models import AlertCandidate, AlertState
from app.scanner.iv_skew_flip.alerts.store import AlertCooldownStore


def make_store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        IVEarlyEntryAlertEvent.__table__, IVEarlyEntryCooldown.__table__,
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
