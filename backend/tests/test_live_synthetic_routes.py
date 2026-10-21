from types import SimpleNamespace
import time

from app.scanner import live_synthetic_routes


def _result():
    option = SimpleNamespace(
        underlying="NIFTY",
        instrument_class="INDEX",
        expiry=20260930,
        strike=25000.0,
        timestamp_ns=time.time_ns(),
        call_bid=100.0,
        call_ask=101.0,
        put_bid=90.0,
        put_ask=91.0,
    )
    future = SimpleNamespace(bid=25100.0, ask=25101.0, lot_size=65)
    return SimpleNamespace(option=option, future=future, direction="LONG", executable_edge=5.0,
                           edge_per_lot=325.0, gross_pnl=325.0, strike_distance=1, strike_side="ABOVE")


def test_live_route_serializes_latest_results(monkeypatch):
    result = _result()
    live_synthetic_routes.configure(lambda: (result,))
    response = live_synthetic_routes.live(limit=10)
    assert response["scanner"] == "synthetic-cash-carry-live-1s"
    assert response["opportunity_count"] == 1
    assert response["data"][0]["underlying"] == "NIFTY"
    assert response["data"][0]["expiry"] == "2026-09-30"
    assert response["data"][0]["executable_edge"] == 5.0


def test_live_route_deduplicates_same_contract_and_direction_by_newest_timestamp():
    older, newer = _result(), _result()
    now_ns = time.time_ns()
    older.option.timestamp_ns = now_ns - 1_000_000_000
    older.future.bid = 25090.0
    newer.option.timestamp_ns = now_ns
    newer.future.bid = 25100.0
    live_synthetic_routes.configure(lambda: (older, newer))
    response = live_synthetic_routes.live(limit=10)
    assert len(response["data"]) == 1
    assert response["data"][0]["timestamp_ns"] == now_ns
    assert response["data"][0]["future_bid"] == 25100.0


def test_live_route_normalizes_numeric_strike_identity():
    text_strike, numeric_strike = _result(), _result()
    text_strike.option.strike = "25000"
    text_strike.option.timestamp_ns = time.time_ns() - 1_000_000
    numeric_strike.option.strike = 25000.0
    numeric_strike.option.timestamp_ns = time.time_ns()
    live_synthetic_routes.configure(lambda: (text_strike, numeric_strike))
    response = live_synthetic_routes.live(limit=10)
    assert len(response["data"]) == 1
    assert response["data"][0]["strike"] == 25000.0


def test_live_route_keeps_different_directions_for_same_contract():
    long_result, short_result = _result(), _result()
    short_result.direction = "SHORT"
    live_synthetic_routes.configure(lambda: (long_result, short_result))
    response = live_synthetic_routes.live(limit=10)
    assert len(response["data"]) == 2
    assert {row["direction"] for row in response["data"]} == {"LONG", "SHORT"}


def test_live_route_skips_malformed_timestamps_without_failing():
    valid, malformed = _result(), _result()
    malformed.option.timestamp_ns = "not-a-timestamp"
    live_synthetic_routes.configure(lambda: (malformed, valid))
    response = live_synthetic_routes.live(limit=10)
    assert len(response["data"]) == 1
    assert response["data"][0]["underlying"] == "NIFTY"


def test_live_route_fairly_distributes_limited_rows_across_underlyings():
    index_rows, stock_rows = [], []
    for strike in range(25000, 25005):
        index_row = _result()
        index_row.option.underlying = "SENSEX"
        index_row.option.instrument_class = "INDEX"
        index_row.option.strike = strike
        index_row.option.timestamp_ns = time.time_ns()
        index_rows.append(index_row)
        stock_row = _result()
        stock_row.option.underlying = "RELIANCE"
        stock_row.option.instrument_class = "STOCK"
        stock_row.option.strike = strike
        stock_row.option.timestamp_ns = time.time_ns()
        stock_rows.append(stock_row)
    live_synthetic_routes.configure(lambda: tuple(index_rows + stock_rows))
    response = live_synthetic_routes.live(limit=4)
    symbols = [row["underlying"] for row in response["data"]]
    assert len(symbols) == 4
    assert symbols.count("SENSEX") == 2
    assert symbols.count("RELIANCE") == 2


def test_live_route_limits_results():
    result = _result()
    live_synthetic_routes.configure(lambda: (result, result))
    response = live_synthetic_routes.live(limit=1)
    assert response["opportunity_count"] == 1


def test_diagnostics_endpoint_is_available():
    response = live_synthetic_routes.diagnostics()
    assert response["scanner"] == "synthetic-cash-carry-live-1s"


def test_pairs_endpoint_returns_empty_snapshot():
    live_synthetic_routes.configure_pairs(lambda: {"status": "success", "data": []})
    response = live_synthetic_routes.pairs(limit=10)
    assert response["count"] == 0
    assert response["data"] == []


def test_pairs_endpoint_sanitizes_snapshot_rows():
    live_synthetic_routes.configure_pairs(lambda: {
        "status": "success",
        "data": [{
            "timestamp_ns": 123, "underlying": "NIFTY", "expiry": 20261029, "strike": 25000,
            "instrument_class": "INDEX",
            "future_bid": 25099.0, "future_ask": 25101.0,
            "call_bid": 100.0, "call_ask": 101.0, "put_bid": 99.0, "put_ask": 100.0,
            "edge_per_lot": 125.0, "gross_pnl": 125.0, "strike_distance": 0.0, "strike_side": "ATM",
            "state": "INCOMPLETE", "reason": "AWAITING_SCANNER_QUALIFICATION",
            "readiness_scope": "STRUCTURAL_ONLY", "opportunity_confirmed": False,
            "qualification_status": "NOT_CONFIRMED", "raw_broker_payload": {"secret": "must not escape"},
        }],
        "summary": {
            "total_rows": 1, "symbols": {"NIFTY": {"rows": 1, "future_quotes": 1}},
            "blocker_counts": {"MISSING_CALL": 1}, "readiness_scope": "STRUCTURAL_ONLY",
            "opportunity_confirmed": False, "raw_broker_payload": {"secret": "must not escape"},
        },
        "raw_broker_payload": {"secret": "must not escape"},
    })
    response = live_synthetic_routes.pairs(limit=10)
    assert response["count"] == 1
    assert response["data"][0]["underlying"] == "NIFTY"
    assert response["data"][0]["reason"] == "AWAITING_SCANNER_QUALIFICATION"
    assert response["data"][0]["readiness_scope"] == "STRUCTURAL_ONLY"
    assert response["data"][0]["opportunity_confirmed"] is False
    assert response["data"][0]["qualification_status"] == "NOT_CONFIRMED"
    assert response["data"][0]["instrument_class"] == "INDEX"
    assert response["data"][0]["future_bid"] == 25099.0
    assert response["data"][0]["call_bid"] == 100.0
    assert response["data"][0]["put_ask"] == 100.0
    assert response["summary"]["total_rows"] == 1
    assert response["summary"]["blocker_counts"] == {"MISSING_CALL": 1}
    assert response["summary"]["opportunity_confirmed"] is False
    assert "raw_broker_payload" not in response
    assert "raw_broker_payload" not in response["summary"]
    assert response["data"][0]["edge_per_lot"] == 125.0
    assert response["data"][0]["strike_side"] == "ATM"
    assert "raw_broker_payload" not in response["data"][0]


def test_pairs_endpoint_normalizes_common_broker_expiry_formats():
    formats = ("20261029", "29OCT2026", "29OCT26", "29-10-2026", "2026-10-29")
    live_synthetic_routes.configure_pairs(lambda: {"status": "success", "data": [
        {"underlying": "NIFTY", "expiry": expiry, "strike": 25000} for expiry in formats
    ]})
    response = live_synthetic_routes.pairs(limit=10)
    assert [row["expiry"] for row in response["data"]] == ["2026-10-29"] * len(formats)


def test_dashboard_synthetic_results_exclude_stale_entries():
    from app.main import _fresh_live_synthetic_results
    fresh, stale = _result(), _result()
    stale.option.timestamp_ns = time.time_ns() - 10_000_000_000
    assert _fresh_live_synthetic_results((fresh, stale)) == (fresh,)
    assert _fresh_live_synthetic_results((stale,)) == ()


def test_diagnostics_summarizes_calculations_and_top_blockers():
    live_synthetic_routes.configure_diagnostics(lambda: {
        "running": True, "phase": "SCANNING", "phase_reason": None, "targets": 16,
        "scanner_initialized": True,
        "subscription_diagnostics": {
            "target_count": 2, "subscribed_symbol_count": 1, "blocked_symbol_count": 1,
            "symbols": [
                {"underlying": "RELIANCE", "status": "SUBSCRIBED"},
                {"underlying": "TCS", "status": "WAITING_FOR_ATM", "reason": "live_atm_unavailable_or_stale"},
            ],
        },
        "scanner_diagnostics": {"counters": {
            "last_observation_at_ns": time.time_ns(), "last_successful_calculation_at_ns": time.time_ns(),
            "last_result_count": 0, "scan_evaluations": 12, "successful_pairs": 7,
            "opportunities_emitted": 0, "empty_calculations": 7, "timestamp_mismatch": 9,
            "missing_atm_price": 4, "scan_calculation_errors": 3,
            "scan_evaluations_by_symbol:RELIANCE": 4,
            "successful_pairs_by_symbol:RELIANCE": 2,
            "opportunities_emitted_by_symbol:RELIANCE": 1,
            "empty_calculations_by_symbol:RELIANCE": 3,
            "missing_atm_price_by_symbol:RELIANCE": 2, "unrelated_counter": 100,
        }},
    })
    response = live_synthetic_routes.diagnostics()
    assert response["calculation_summary"] == {"evaluations": 12, "successful_pairs": 7, "opportunities_emitted": 0,
                                                "empty_calculations": 7, "last_result_count": 0}
    assert response["top_blockers"] == [
        {"reason": "timestamp_mismatch", "count": 9}, {"reason": "missing_atm_price", "count": 4},
        {"reason": "scan_calculation_errors", "count": 3},
        {"reason": "missing_atm_price_by_symbol:RELIANCE", "count": 2},
    ]
    # Symbol-scoped outcome counters remain available to the dashboard.
    assert response["counters"]["scan_evaluations_by_symbol:RELIANCE"] == 4
    assert response["counters"]["successful_pairs_by_symbol:RELIANCE"] == 2
    assert response["counters"]["opportunities_emitted_by_symbol:RELIANCE"] == 1
    assert response["counters"]["empty_calculations_by_symbol:RELIANCE"] == 3
    assert "unrelated_counter" not in [item["reason"] for item in response["top_blockers"]]
    assert "cumulative" in response["blocker_counter_note"]
    assert response["subscription_diagnostics"]["subscribed_symbol_count"] == 1
    assert response["subscription_diagnostics"]["symbols"][1]["underlying"] == "TCS"


def test_diagnostics_exposes_atm_chain_readiness_without_raw_payloads():
    now = time.time_ns()
    live_synthetic_routes.configure_diagnostics(lambda: {
        "running": True,
        "scanner_diagnostics": {"counters": {"last_observation_at_ns": now, "last_successful_calculation_at_ns": now}},
        "atm_tracker_diagnostics": {
            "tracked_symbol_count": 2,
            "atm_ready_symbol_count": 1,
            "symbols_without_concrete_chain": ["TCS"],
            "symbols": [
                {"underlying": "RELIANCE", "ready": True, "concrete_strike_count": 7,
                 "underlying_price_present": True, "source_age_seconds": 0.2, "atm_strike": 25000},
                {"underlying": "TCS", "ready": False, "concrete_strike_count": 0,
                 "underlying_price_present": False, "source_age_seconds": None, "reason": "underlying_price_unavailable"},
            ],
        },
    })
    response = live_synthetic_routes.diagnostics()
    assert response["atm_tracker_diagnostics"]["symbols_without_concrete_chain"] == ["TCS"]
    assert response["atm_readiness_summary"]["ready_symbols"] == 1
    assert response["atm_readiness_summary"]["ready_underlyings"] == ["RELIANCE"]
    assert response["atm_readiness_summary"]["not_ready_symbols"] == [{
        "underlying": "TCS", "reason": "underlying_price_unavailable", "concrete_strike_count": 0,
        "underlying_price_present": False, "underlying_price": None, "atm_strike": None, "source_age_seconds": None,
    }]
    assert "raw_broker_payload" not in response


def test_atm_readiness_summary_accepts_live_tracker_field_names():
    live_synthetic_routes.configure_diagnostics(lambda: {
        "running": True,
        "scanner_diagnostics": {"counters": {}},
        "atm_tracker_diagnostics": {
            "symbols": [
                {"underlying": "RELIANCE", "atm_ready": True, "concrete_strike_count": 9,
                 "has_underlying_price": True, "last_underlying_price": 1482.5,
                 "source_age_seconds": 0.4, "atm_strike": 1480},
                {"underlying": "TCS", "atm_ready": False, "concrete_strike_count": 0,
                 "has_underlying_price": False, "last_underlying_price": None,
                 "source_age_seconds": None, "atm_strike": None},
            ],
        },
    })
    response = live_synthetic_routes.diagnostics()
    summary = response["atm_readiness_summary"]
    assert summary["ready_symbols"] == 1
    assert summary["ready_underlyings"] == ["RELIANCE"]
    assert summary["not_ready_symbols"] == [{
        "underlying": "TCS",
        "reason": "underlying_price_unavailable",
        "concrete_strike_count": 0,
        "underlying_price_present": False,
        "underlying_price": None,
        "atm_strike": None,
        "source_age_seconds": None,
    }]


def test_live_route_normalizes_string_broker_expiry():
    result = _result()
    result.option.expiry = "29OCT2026"
    live_synthetic_routes.configure(lambda: (result,))
    response = live_synthetic_routes.live(limit=10)
    assert response["data"][0]["expiry"] == "2026-10-29"


def test_live_synthetic_callback_merges_fresh_symbols_instead_of_overwriting(monkeypatch):
    from app import main
    index_result, stock_result = _result(), _result()
    index_result.option.underlying, index_result.option.instrument_class = "SENSEX", "INDEX"
    index_result.option.timestamp_ns = time.time_ns()
    stock_result.option.underlying, stock_result.option.instrument_class = "RELIANCE", "STOCK"
    stock_result.option.expiry, stock_result.option.timestamp_ns = "2026-10-29", time.time_ns()
    monkeypatch.setattr(main, "live_synthetic_latest_results", ())
    main._update_live_synthetic_results((index_result,))
    main._update_live_synthetic_results((stock_result,))
    main._update_live_synthetic_results(())
    assert {row.option.underlying for row in main.live_synthetic_latest_results} == {"SENSEX", "RELIANCE"}



def test_diagnostics_summarizes_pair_blockers_per_underlying():
    live_synthetic_routes.configure_diagnostics(lambda: {
        "running": True,
        "scanner_diagnostics": {"counters": {}},
    })
    live_synthetic_routes.configure_pairs(lambda: {"status": "success", "data": [
        {"underlying": "RELIANCE", "state": "INCOMPLETE", "reason": "MISSING_CALL,MISSING_PUT"},
        {"underlying": "RELIANCE", "state": "INCOMPLETE", "reason": "MISSING_CALL"},
        {"underlying": "TCS", "state": "READY_TO_EVALUATE", "reason": "AWAITING_SCANNER_QUALIFICATION"},
    ]})
    response = live_synthetic_routes.diagnostics()
    summary = response["pair_readiness_summary"]
    assert summary["available"] is True
    assert summary["row_count"] == 3
    assert summary["symbols"] == [
        {
            "underlying": "RELIANCE", "row_count": 2, "ready_to_evaluate_count": 0,
            "incomplete_count": 2,
            "blocker_counts": [{"reason": "MISSING_CALL", "count": 2}, {"reason": "MISSING_PUT", "count": 1}],
        },
        {
            "underlying": "TCS", "row_count": 1, "ready_to_evaluate_count": 1,
            "incomplete_count": 0, "blocker_counts": [],
        },
    ]
