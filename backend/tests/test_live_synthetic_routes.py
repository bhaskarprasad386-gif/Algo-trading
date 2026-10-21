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
    future = SimpleNamespace(
        bid=25100.0,
        ask=25101.0,
        lot_size=65,
    )
    return SimpleNamespace(
        option=option,
        future=future,
        direction="LONG",
        executable_edge=5.0,
        edge_per_lot=325.0,
        gross_pnl=325.0,
        strike_distance=1,
        strike_side="ABOVE",
    )


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
    older = _result()
    newer = _result()
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


def test_live_route_keeps_different_directions_for_same_contract():
    long_result = _result()
    short_result = _result()
    short_result.direction = "SHORT"
    live_synthetic_routes.configure(lambda: (long_result, short_result))

    response = live_synthetic_routes.live(limit=10)

    assert len(response["data"]) == 2
    assert {row["direction"] for row in response["data"]} == {"LONG", "SHORT"}


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
    live_synthetic_routes.configure_pairs(
        lambda: {
            "status": "success",
            "data": [{
                "timestamp_ns": 123,
                "underlying": "NIFTY",
                "expiry": 20261029,
                "strike": 25000,
                "state": "INCOMPLETE",
                "reason": "AWAITING_SCANNER_QUALIFICATION",
                "readiness_scope": "STRUCTURAL_ONLY",
                "opportunity_confirmed": False,
                "qualification_status": "NOT_CONFIRMED",
                "raw_broker_payload": {"secret": "must not escape"},
            }],
        }
    )
    response = live_synthetic_routes.pairs(limit=10)
    assert response["count"] == 1
    assert response["data"][0]["underlying"] == "NIFTY"
    assert response["data"][0]["reason"] == "AWAITING_SCANNER_QUALIFICATION"
    assert response["data"][0]["readiness_scope"] == "STRUCTURAL_ONLY"
    assert response["data"][0]["opportunity_confirmed"] is False
    assert response["data"][0]["qualification_status"] == "NOT_CONFIRMED"
    assert "raw_broker_payload" not in response["data"][0]

def test_pairs_endpoint_normalizes_common_broker_expiry_formats():
    formats = ("20261029", "29OCT2026", "29OCT26", "29-10-2026", "2026-10-29")
    live_synthetic_routes.configure_pairs(
        lambda: {
            "status": "success",
            "data": [
                {"underlying": "NIFTY", "expiry": expiry, "strike": 25000}
                for expiry in formats
            ],
        }
    )

    response = live_synthetic_routes.pairs(limit=10)

    assert [row["expiry"] for row in response["data"]] == ["2026-10-29"] * len(formats)

def test_dashboard_synthetic_results_exclude_stale_entries():
    from app.main import _fresh_live_synthetic_results

    fresh = _result()
    stale = _result()
    stale.option.timestamp_ns = time.time_ns() - 10_000_000_000

    assert _fresh_live_synthetic_results((fresh, stale)) == (fresh,)
    assert _fresh_live_synthetic_results((stale,)) == ()



def test_diagnostics_summarizes_calculations_and_top_blockers():
    live_synthetic_routes.configure_diagnostics(
        lambda: {
            "running": True,
            "phase": "SCANNING",
            "phase_reason": None,
            "targets": 16,
            "scanner_initialized": True,
            "scanner_diagnostics": {
                "counters": {
                    "last_observation_at_ns": time.time_ns(),
                    "last_successful_calculation_at_ns": time.time_ns(),
                    "last_result_count": 0,
                    "scan_evaluations": 12,
                    "successful_pairs": 7,
                    "opportunities_emitted": 0,
                    "empty_calculations": 7,
                    "timestamp_mismatch": 9,
                    "missing_atm_price": 4,
                    "scan_calculation_errors": 3,
                    "missing_atm_price_by_symbol:RELIANCE": 2,
                    "unrelated_counter": 100,
                }
            },
        }
    )
    response = live_synthetic_routes.diagnostics()
    assert response["calculation_summary"] == {
        "evaluations": 12,
        "successful_pairs": 7,
        "opportunities_emitted": 0,
        "empty_calculations": 7,
        "last_result_count": 0,
    }
    assert response["top_blockers"] == [
        {"reason": "timestamp_mismatch", "count": 9},
        {"reason": "missing_atm_price", "count": 4},
        {"reason": "scan_calculation_errors", "count": 3},
        {"reason": "missing_atm_price_by_symbol:RELIANCE", "count": 2},
    ]
    assert "unrelated_counter" not in [item["reason"] for item in response["top_blockers"]]
    assert "cumulative" in response["blocker_counter_note"]

def test_live_route_normalizes_string_broker_expiry():
    result = _result()
    result.option.expiry = "29OCT2026"
    live_synthetic_routes.configure(lambda: (result,))

    response = live_synthetic_routes.live(limit=10)

    assert response["data"][0]["expiry"] == "2026-10-29"
