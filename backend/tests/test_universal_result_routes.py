from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtesting.result_ledger import (
    BacktestEvent,
    BacktestFill,
    BacktestResultLedger,
    BacktestTrade,
    EquityPoint,
)
from app.backtesting.universal_result_routes import create_universal_result_router
from app.backtesting.universal_result_service import UniversalResultService


def _client() -> tuple[TestClient, BacktestResultLedger]:
    ledger = BacktestResultLedger(":memory:")
    app = FastAPI()
    app.include_router(create_universal_result_router(UniversalResultService(ledger)))
    return TestClient(app), ledger


def _completed_run(ledger: BacktestResultLedger, run_id: str = "run-api") -> None:
    ledger.create_run(run_id, {"initial_capital": 100_000.0})
    ledger.append_events(
        run_id,
        [BacktestEvent(i, 1_000 + i, "EVENT", {"i": i}) for i in range(3)],
    )
    ledger.append_fills(
        run_id,
        [
            BacktestFill("fill-0", "order-0", 0, 1_000, "NIFTY", "BUY", 1, 100.0),
            BacktestFill("fill-1", "order-1", 1, 1_001, "NIFTY", "SELL", 1, 101.0),
        ],
    )
    ledger.append_trades(
        run_id,
        [
            BacktestTrade("trade-0", 0, 1_000, "NIFTY", "BUY", 1, 100.0, 101.0, 1.0, 0.1, 0.0, 0.9),
            BacktestTrade("trade-1", 1, 1_001, "NIFTY", "SELL", 1, 101.0, 102.0, 1.0, 0.1, 0.0, 0.9),
        ],
    )
    ledger.append_equity(
        run_id,
        [
            EquityPoint(1_000, 100_000.0, 0.0, 0.0, 0.0),
            EquityPoint(1_000, 100_010.0, 10.0, 0.0, 0.0),
            EquityPoint(2_000, 100_020.0, 20.0, 0.0, 0.0),
        ],
    )
    ledger.set_status(run_id, "COMPLETED")


def test_summary_completed_run_is_200_and_unknown_is_404() -> None:
    client, ledger = _client()
    _completed_run(ledger)

    response = client.get("/api/v1/backtesting/universal/runs/run-api")
    assert response.status_code == 200
    assert response.json()["run_id"] == "run-api"

    missing = client.get("/api/v1/backtesting/universal/runs/missing")
    assert missing.status_code == 404


def test_summary_non_completed_run_is_422() -> None:
    client, ledger = _client()
    ledger.create_run("run-open", {"initial_capital": 100_000.0})

    response = client.get("/api/v1/backtesting/universal/runs/run-open")
    assert response.status_code == 422


def test_events_pagination_has_no_duplicate_and_unknown_is_404() -> None:
    client, ledger = _client()
    _completed_run(ledger)

    first = client.get("/api/v1/backtesting/universal/runs/run-api/events?limit=2")
    assert first.status_code == 200
    assert [x["sequence"] for x in first.json()["data"]] == [0, 1]

    cursor = first.json()["next_cursor"]
    second = client.get(
        f"/api/v1/backtesting/universal/runs/run-api/events?limit=2&after_sequence={cursor}"
    )
    assert second.status_code == 200
    assert [x["sequence"] for x in second.json()["data"]] == [2]
    assert second.json()["next_cursor"] is None

    missing = client.get("/api/v1/backtesting/universal/runs/missing/events")
    assert missing.status_code == 404


def test_fills_and_trades_have_sequence_pagination() -> None:
    client, ledger = _client()
    _completed_run(ledger)

    for record_type in ("fills", "trades"):
        first = client.get(
            f"/api/v1/backtesting/universal/runs/run-api/{record_type}?limit=1"
        )
        assert first.status_code == 200
        assert first.json()["record_type"] == record_type
        assert [x["sequence"] for x in first.json()["data"]] == [0]

        cursor = first.json()["next_cursor"]
        second = client.get(
            f"/api/v1/backtesting/universal/runs/run-api/{record_type}"
            f"?limit=1&after_sequence={cursor}"
        )
        assert second.status_code == 200
        assert [x["sequence"] for x in second.json()["data"]] == [1]
        assert second.json()["next_cursor"] is None


def test_empty_collections_are_bounded_and_have_no_cursor() -> None:
    client, ledger = _client()
    ledger.create_run("run-empty", {"initial_capital": 100_000.0})

    for record_type in ("events", "fills", "trades", "equity"):
        response = client.get(
            f"/api/v1/backtesting/universal/runs/run-empty/{record_type}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["count"] == 0
        assert body["next_cursor"] is None


def test_equity_composite_cursor_and_half_cursor_are_enforced() -> None:
    client, ledger = _client()
    _completed_run(ledger)

    first = client.get("/api/v1/backtesting/universal/runs/run-api/equity?limit=1")
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]

    second = client.get(
        "/api/v1/backtesting/universal/runs/run-api/equity"
        f"?limit=2&after_timestamp_ns={cursor['timestamp_ns']}"
        f"&after_equity_id={cursor['equity_id']}"
    )
    assert second.status_code == 200
    rows = second.json()["data"]
    assert rows[0]["timestamp_ns"] == 1_000
    assert rows[0]["equity_id"] > cursor["equity_id"]

    half = client.get(
        "/api/v1/backtesting/universal/runs/run-api/equity?after_timestamp_ns=1000"
    )
    assert half.status_code == 422


def test_invalid_limits_and_negative_cursors_are_422() -> None:
    client, ledger = _client()
    _completed_run(ledger)

    assert client.get(
        "/api/v1/backtesting/universal/runs/run-api/events?limit=0"
    ).status_code == 422
    assert client.get(
        "/api/v1/backtesting/universal/runs/run-api/events?after_sequence=-2"
    ).status_code == 422
    assert client.get(
        "/api/v1/backtesting/universal/runs/run-api/equity?after_timestamp_ns=-2&after_equity_id=-2"
    ).status_code == 422


def test_route_run_isolation() -> None:
    client, ledger = _client()
    _completed_run(ledger, "run-a")
    ledger.create_run("run-b", {"initial_capital": 200_000.0})
    ledger.append_events("run-b", [BacktestEvent(0, 9_000, "EVENT", {"run": "b"})])

    response = client.get("/api/v1/backtesting/universal/runs/run-b/events")
    assert response.status_code == 200
    assert response.json()["data"][0]["payload_json"] != '{"i":0}'


def test_route_isolation_keeps_legacy_namespace_untouched() -> None:
    client, _ = _client()
    assert client.get("/api/v1/backtesting/runs/anything").status_code == 404
