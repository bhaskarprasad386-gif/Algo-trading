from app.backtesting.advanced_analytics import (
    AuditEvent,
    AuditReplay,
    MfeMae,
    OpportunityAnalytics,
    OpportunityPoint,
    StressScenario,
    WalkForwardConfig,
    apply_stress,
    monte_carlo,
    robustness_sweep,
    walk_forward_splits,
)
from app.backtesting.multi_venue_discovery import MultiVenueInstrumentDiscovery


def test_phase7_maximum_gap_and_audit_replay_are_source_deterministic():
    points = (
        OpportunityPoint(300, 8.0, 6.0),
        OpportunityPoint(100, 5.0, 4.0),
        OpportunityPoint(200, 10.0, 7.0),
        OpportunityPoint(400, 2.0, 0.0),
    )
    result = OpportunityAnalytics.analyze(points, minimum_executable_spread=4.0)
    assert result.maximum.timestamp_ns == 200
    assert result.executable_maximum.timestamp_ns == 200
    assert result.windows[0].start_ns == 100
    assert result.windows[0].end_ns == 300

    events = (
        AuditEvent(2, 200, "signal", "NIFTY"),
        AuditEvent(1, 100, "quote", "NIFTY"),
        AuditEvent(3, 300, "order", "NIFTY"),
    )
    assert [e.sequence for e in AuditReplay.explain(events, timestamp_ns=200)] == [1, 2]


def test_phase7_mfe_mae_and_phase8_walk_forward_are_reproducible():
    excursion = MfeMae.from_path(100.0, [98.0, 105.0, 102.0])
    assert excursion.maximum_favorable_excursion == 0.05
    assert excursion.maximum_adverse_excursion == -0.02

    config = WalkForwardConfig(train_size=4, validation_size=2, test_size=2, step=2)
    splits = walk_forward_splits(10, config)
    assert splits[0].train_start == 0
    assert splits[0].train_end == 4
    assert splits[0].validation_end == 6
    assert splits[0].test_end == 8
    assert len(splits) == 2

    sweep = robustness_sweep("slippage_bps", [1, 2, 3], lambda x: 10.0 - x)
    assert [p.score for p in sweep] == [9.0, 8.0, 7.0]

    mc1 = monte_carlo((1.0, 2.0, 3.0), runs=100, seed=7)
    mc2 = monte_carlo((1.0, 2.0, 3.0), runs=100, seed=7)
    assert mc1 == mc2
    assert mc1.runs == 100

    scenario = StressScenario("high-slippage", slippage_multiplier=2.0, latency_multiplier=1.5)
    assert apply_stress(100.0, scenario) < 100.0


def test_phase9_uses_one_normalized_contract_for_bse_stock_and_mcx_commodity():
    rows = [
        {
            "token": "B1",
            "symbol": "SBIN30SEP26FUT",
            "name": "SBIN",
            "expiry": "30SEP2026",
            "lotsize": "750",
            "instrumenttype": "FUTSTK",
            "exch_seg": "BFO",
        },
        {
            "token": "M1",
            "symbol": "GOLD30SEP26FUT",
            "name": "GOLD",
            "expiry": "30SEP2026",
            "lotsize": "100",
            "instrumenttype": "FUTCOM",
            "exch_seg": "MCX",
        },
        {
            "token": "N1",
            "symbol": "NIFTY30SEP26FUT",
            "name": "NIFTY",
            "expiry": "30SEP2026",
            "lotsize": "75",
            "instrumenttype": "FUTIDX",
            "exch_seg": "NFO",
        },
    ]
    result = MultiVenueInstrumentDiscovery.discover(rows)
    assert {(x.venue, x.instrument_class) for x in result} == {
        ("BSE", "STOCK"),
        ("MCX", "COMMODITY"),
        ("NSE", "INDEX"),
    }
    assert len(result) == 3
