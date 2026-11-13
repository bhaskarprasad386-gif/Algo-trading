from app.scanner.iv_skew_flip.alerts.orchestrator import IVEarlyEntryAlertEngine


def test_alert_engine_has_no_time_of_day_strategy_window():
    # Strategy conditions, not clock windows, decide when an alert is emitted.
    assert not hasattr(IVEarlyEntryAlertEngine, "_in_window")



from datetime import datetime, timezone
from types import SimpleNamespace


class _LifecycleStore:
    def __init__(self):
        self.invalidations = []
        self.expirations = []

    def expire_session_alerts(self, *, now):
        self.expirations.append(now)
        return 0

    def invalidate_breached_oi_walls(self, *, spot, observed_at_ns):
        self.invalidations.append((spot, observed_at_ns))
        return 0


class _NotLiveSampler:
    def snapshot(self):
        return {"status": "DEGRADED", "live_samples": []}


def test_stale_cached_spot_cannot_invalidate_active_oi_wall_alert():
    now_ns = 1_800_000_000 * 1_000_000_000
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=timezone.utc)
    store = _LifecycleStore()
    engine = IVEarlyEntryAlertEngine(store)
    stale_spot = SimpleNamespace(
        timestamp_ns=now_ns - 10_000_000_000,
        price=24900.0,
    )

    result = engine.evaluate(
        sampler=_NotLiveSampler(), spot=stale_spot, expiry="2026-11-19",
        now=now, now_ns=now_ns,
    )

    assert result["reason"] == "SAMPLER_NOT_LIVE"
    assert store.invalidations == []


def test_fresh_spot_tick_can_invalidate_oi_wall_alert():
    now_ns = 1_800_000_000 * 1_000_000_000
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=timezone.utc)
    store = _LifecycleStore()
    engine = IVEarlyEntryAlertEngine(store)
    fresh_spot = SimpleNamespace(
        timestamp_ns=now_ns - 2_000_000_000,
        price=24900.0,
    )

    engine.evaluate(
        sampler=_NotLiveSampler(), spot=fresh_spot, expiry="2026-11-19",
        now=now, now_ns=now_ns,
    )

    assert store.invalidations == [(24900.0, fresh_spot.timestamp_ns)]
