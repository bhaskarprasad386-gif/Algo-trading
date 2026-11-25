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

def test_banknifty_early_entry_structural_guard_accepts_bank_contract_identity():
    from types import SimpleNamespace
    from app.scanner.iv_skew_flip.alerts.conditions import _structurally_valid

    bank_observation = SimpleNamespace(
        token="BANK-58000-CE", expiry="2026-11-26", underlying="BANKNIFTY",
        option_type="CE", source_timestamp_ns=100, spot_timestamp_ns=100,
        spot_price=58100.0, iv=18.5, iv_input="BID_ASK_MIDPOINT",
        bid=120.0, ask=121.0, oi=50000, oi_timestamp_ns=100, volume=1000,
    )

    assert _structurally_valid(bank_observation, "BANKNIFTY") == ()
    assert "WRONG_UNIVERSE" in _structurally_valid(bank_observation, "NIFTY")

def test_lifecycle_transition_refreshes_cached_history_even_when_sampler_is_degraded():
    class _LifecycleHistoryStore(_LifecycleStore):
        def __init__(self):
            super().__init__()
            self.latest_calls = []
            self.expirations_result = 1

        def expire_session_alerts(self, *, now):
            self.expirations.append(now)
            return self.expirations_result

        def latest(self, *, limit=20, underlying=None):
            self.latest_calls.append((limit, underlying))
            return [{
                "id": 41, "underlying": underlying, "state": "EXPIRED",
                "reason": "PRIOR_SESSION_ALERT",
            }]

    now_ns = 1_800_000_000 * 1_000_000_000
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=timezone.utc)
    store = _LifecycleHistoryStore()
    engine = IVEarlyEntryAlertEngine(store)
    engine._history_loaded.add("BANKNIFTY")
    engine._last_results["BANKNIFTY_MONTHLY"] = {
        "state": "WATCH", "underlying": "BANKNIFTY",
        "alerts": [{"id": 41, "underlying": "BANKNIFTY", "state": "EARLY_ENTRY"}],
    }

    result = engine.evaluate(
        sampler=_NotLiveSampler(),
        spot=SimpleNamespace(timestamp_ns=now_ns - 10_000_000_000, price=58100.0),
        expiry="2026-11-26", now=now, now_ns=now_ns,
        underlying="BANKNIFTY", strike_step=100.0,
    )

    assert result["reason"] == "SAMPLER_NOT_LIVE"
    assert store.latest_calls == [(30, "BANKNIFTY")]
    assert engine.snapshot("BANKNIFTY")["alerts"][0]["state"] == "EXPIRED"
    assert engine.snapshot("NIFTY")["underlying"] == "NIFTY"



def test_expiry_lifecycle_sweep_is_throttled_per_underlying_and_retries_after_interval():
    now_ns = 1_800_000_000 * 1_000_000_000
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=timezone.utc)
    store = _LifecycleStore()
    engine = IVEarlyEntryAlertEngine(store)
    stale_spot = SimpleNamespace(
        timestamp_ns=now_ns - 10_000_000_000,
        price=24900.0,
    )

    for offset, underlying in (
        (0, "NIFTY"), (1_000_000_000, "BANKNIFTY"),
        (30_000_000_000, "NIFTY"), (30_000_000_000, "BANKNIFTY"),
    ):
        sample_ns = now_ns + offset
        engine.evaluate(
            sampler=_NotLiveSampler(), spot=stale_spot,
            expiry="2026-11-19" if underlying == "NIFTY" else "2026-11-26",
            now=datetime.fromtimestamp(sample_ns / 1_000_000_000, tz=timezone.utc),
            now_ns=sample_ns, underlying=underlying,
        )

    # The store sweep spans both indices; the second underlying must not
    # trigger an identical full-table expiry query in the same minute.
    assert len(store.expirations) == 1

    next_ns = now_ns + 60_000_000_000
    engine.evaluate(
        sampler=_NotLiveSampler(), spot=stale_spot, expiry="2026-11-19",
        now=datetime.fromtimestamp(next_ns / 1_000_000_000, tz=timezone.utc),
        now_ns=next_ns,
    )
    assert len(store.expirations) == 2


def test_expiry_sweep_failure_is_retried_without_advancing_watermark():
    class _FailOnceStore(_LifecycleStore):
        def __init__(self):
            super().__init__()
            self.fail = True

        def expire_session_alerts(self, *, now):
            self.expirations.append(now)
            if self.fail:
                self.fail = False
                raise RuntimeError("temporary database outage")
            return 0

    now_ns = 1_800_000_000 * 1_000_000_000
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=timezone.utc)
    store = _FailOnceStore()
    engine = IVEarlyEntryAlertEngine(store)
    spot = SimpleNamespace(timestamp_ns=now_ns - 10_000_000_000, price=24900.0)

    import pytest
    with pytest.raises(RuntimeError, match="temporary database outage"):
        engine.evaluate(
            sampler=_NotLiveSampler(), spot=spot, expiry="2026-11-19",
            now=now, now_ns=now_ns,
        )

    result = engine.evaluate(
        sampler=_NotLiveSampler(), spot=spot, expiry="2026-11-19",
        now=now, now_ns=now_ns,
    )
    assert result["reason"] == "SAMPLER_NOT_LIVE"
    assert len(store.expirations) == 2
