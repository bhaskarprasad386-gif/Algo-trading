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

    def invalidate_breached_oi_walls(self, *, spot, observed_at_ns, underlying=None):
        self.invalidations.append((spot, observed_at_ns, underlying))
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

    assert store.invalidations == [(24900.0, fresh_spot.timestamp_ns, "NIFTY")]

def test_alert_engine_keeps_nifty_and_banknifty_histories_isolated():
    now_ns = 1_800_000_000 * 1_000_000_000
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=timezone.utc)
    store = _LifecycleStore()
    engine = IVEarlyEntryAlertEngine(store)
    nifty_spot = SimpleNamespace(timestamp_ns=now_ns, price=24900.0)
    bank_spot = SimpleNamespace(timestamp_ns=now_ns, price=58100.0)

    nifty = engine.evaluate(
        sampler=_NotLiveSampler(), spot=nifty_spot, expiry="2026-11-19",
        now=now, now_ns=now_ns, underlying="NIFTY", strike_step=50.0,
    )
    bank = engine.evaluate(
        sampler=_NotLiveSampler(), spot=bank_spot, expiry="2026-11-26",
        now=now, now_ns=now_ns, underlying="BANKNIFTY", strike_step=100.0,
        max_consolidation_points=110.0, wall_tolerance_points=30.0,
    )

    assert nifty["reason"] == bank["reason"] == "SAMPLER_NOT_LIVE"
    assert engine.snapshot("NIFTY")["underlying"] == "NIFTY"
    assert engine.snapshot("BANKNIFTY")["underlying"] == "BANKNIFTY"
    assert engine.snapshot_all().keys() == {"NIFTY", "BANKNIFTY"}
    assert store.invalidations == [
        (24900.0, nifty_spot.timestamp_ns, "NIFTY"),
        (58100.0, bank_spot.timestamp_ns, "BANKNIFTY"),
    ]


def test_alert_strike_grid_supports_banknifty_hundred_point_contracts():
    from app.scanner.iv_skew_flip.alerts.conditions import even_strikes

    assert even_strikes([58000, 58050, 58100, 58150, 58200], 100.0) == (58000.0, 58100.0, 58200.0)
    assert even_strikes([24900, 24950, 25000], 50.0) == (24900.0, 24950.0, 25000.0)

