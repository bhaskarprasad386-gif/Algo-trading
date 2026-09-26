from datetime import date, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtesting.cash_future_strategy_routes import (
    StrategyRunRequest,
    _build_builder_strategy,
    _gap_threshold_implementation_hash,
    router,
)
from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.backtesting.ledger import BacktestLedger, LedgerRecord
from app.scanner.cash_future_history import CashFutureHistoryPoint
from app.backtesting.provenance import provenance_hash
from app.core.config import settings


from app.backtesting.cash_future_strategy_routes import (
    StrategyRunRequest,
    _build_builder_strategy,
    _gap_threshold_implementation_hash,
    router,
)
from app.backtesting.ledger import BacktestLedger, LedgerRecord
from app.backtesting.provenance import provenance_hash
from app.core.config import settings


def payload(*, gap: float, timestamp: datetime, contract_month: str = "SEP"):
    return {
        "timestamp": timestamp.isoformat(),
        "symbol": "ABC",
        "contract_month": contract_month,
        "cash_price": 100.0,
        "future_price": 100.0 + gap,
        "gap": gap,
        "gap_pct": gap,
        "lot_size": 100,
        "margin_required": 10000.0,
        "expiry_date": "2026-09-30",
    }


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_strategy_run_route_executes_historical_buy_sell():
    start = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
            "initial_capital": 10_000_000,
            "target": 5.0,
            "stop_loss": 2.0,
            "points": [
                payload(gap=10, timestamp=start),
                payload(gap=4, timestamp=start + timedelta(hours=1)),
                payload(gap=3, timestamp=start + timedelta(days=1)),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["strategy_id"] == "gap_threshold"
    assert body["signal_count"] == 2
    assert body["trade_count"] == 1
    assert body["net_profit"] == 600.0
    assert body["final_capital"] == 10_000_600.0
    assert body["final_available_capital"] == 10_000_600.0
    assert body["final_reserved_margin"] == 0.0
    assert body["blocked_entry_count"] == 0


def test_strategy_run_route_exposes_unrealized_equity_separately_from_final_capital():
    start = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
            "initial_capital": 10_000_000,
            "points": [payload(gap=10, timestamp=start)],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["net_profit"] == 0.0
    assert body["final_capital"] == 10_000_000.0
    assert body["final_available_capital"] == 9_990_000.0
    assert body["final_reserved_margin"] == 10_000.0
    assert body["equity_curve"][-1]["unrealized_pnl"] == 0.0
    assert body["analysis"]["final_equity"] == 10_000_000.0


def test_strategy_run_route_marks_nonzero_unrealized_pnl_on_open_position():
    start = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
            "initial_capital": 10_000_000,
            "target": 5.0,
            "points": [
                payload(gap=10, timestamp=start),
                payload(gap=8, timestamp=start + timedelta(hours=1)),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trade_count"] == 0
    assert body["net_profit"] == 0.0
    assert body["final_capital"] == 10_000_000.0
    assert body["final_available_capital"] == 9_990_000.0
    assert body["final_reserved_margin"] == 10_000.0
    assert body["equity_curve"][-1]["unrealized_pnl"] == 200.0
    assert body["equity_curve"][-1]["equity"] == 10_000_200.0
    assert body["analysis"]["final_equity"] == 10_000_200.0


def test_strategy_run_route_rejects_unknown_strategy():
    now = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "does_not_exist",
            "points": [payload(gap=10, timestamp=now)],
        },
    )
    assert response.status_code == 404
    assert "unknown Cash-Future strategy" in response.json()["detail"]


def test_strategy_run_route_preserves_contract_isolation_error():
    now = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "points": [
                payload(gap=10, timestamp=now, contract_month="SEP"),
                payload(gap=-1, timestamp=now + timedelta(hours=1), contract_month="OCT"),
            ],
        },
    )
    assert response.status_code == 422
    assert "multiple contract months" in response.json()["detail"]


def test_strategy_run_route_returns_output_analysis():
    start = datetime(2026, 9, 2, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
            "initial_capital": 10_000_000,
            "target": 5.0,
            "points": [
                payload(gap=10, timestamp=start),
                payload(gap=4, timestamp=start + timedelta(hours=1)),
            ],
        },
    )

    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis["initial_capital"] == 10_000_000
    assert analysis["final_equity"] == 10_000_600.0
    assert analysis["net_pnl"] == 600.0
    assert analysis["roi"] == 0.00006
    assert analysis["trade_count"] == 1
    assert analysis["wins"] == 1
    assert analysis["losses"] == 0
    assert analysis["profit_factor"] is None
    assert analysis["monthly_pnl"] == {"2026-09": 600.0}
    assert analysis["yearly_pnl"] == {"2026": 600.0}


def test_strategy_run_result_page_returns_bounded_records_and_cursor(monkeypatch, tmp_path):
    ledger_db = str(tmp_path / "ledger.db")
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)
    ledger = BacktestLedger(ledger_db)
    ledger.start_run("page-route", "strategy", "1", 100_000.0)
    for index in range(3):
        ledger.append(LedgerRecord("page-route", "equity", index, {"value": index}))

    from app.backtesting.cash_future_strategy_routes import _result_page

    first = _result_page(ledger, "page-route", "equity", 2, None)
    assert [record["value"] for record in first["data"]] == [0, 1]
    assert first["next_cursor"] is not None
    assert first["total"] == 3

    second = _result_page(
        ledger, "page-route", "equity", 2, first["next_cursor"]
    )
    assert [record["value"] for record in second["data"]] == [2]
    assert second["next_cursor"] is None
    ledger.close()


def test_strategy_run_result_page_rejects_unknown_run():
    ledger = BacktestLedger()
    from app.backtesting.cash_future_strategy_routes import _result_page

    try:
        try:
            _result_page(ledger, "missing", "equity", 10, None)
        except ValueError as exc:
            assert "unknown run_id" in str(exc)
        else:
            raise AssertionError("unknown run must fail")
    finally:
        ledger.close()


def test_strategy_run_persists_provenance_for_direct_points(monkeypatch, tmp_path):
    ledger_db = str(tmp_path / "ledger.db")
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)
    start = datetime(2026, 9, 2, 10, 0)
    request = {
        "strategy_id": "gap_threshold",
        "strategy_version": "1",
        "initial_capital": 10_000_000,
        "points": [payload(gap=10, timestamp=start), payload(gap=4, timestamp=start + timedelta(hours=1))],
        "stop_loss": 2.0,
        "target": 5.0,
    }
    response = client().post("/api/v1/backtesting/cash-future/strategy-run", json=request)
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    ledger = BacktestLedger(ledger_db)
    try:
        metadata = ledger.run_metadata(run_id)
        assert metadata is not None
        assert len(metadata["strategy_hash"]) == 64
        assert len(metadata["data_source_fingerprint"]) == 64
        assert len(metadata["metadata"]["strategy_config_hash"]) == 64
    finally:
        ledger.close()



def test_strategy_run_route_honors_backdate_start_and_end_dates():
    day1 = datetime(2026, 9, 1, 10, 0)
    day2 = datetime(2026, 9, 2, 10, 0)
    day3 = datetime(2026, 9, 3, 10, 0)
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": "2026-09-02",
            "end_date": "2026-09-02",
            "initial_capital": 10_000_000,
            "target": 5.0,
            "points": [
                payload(gap=10, timestamp=day1),
                payload(gap=10, timestamp=day2),
                payload(gap=4, timestamp=day2 + timedelta(hours=1)),
                payload(gap=1, timestamp=day3),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["signal_count"] == 2
    assert body["trade_count"] == 1
    assert body["net_profit"] == 600.0
    assert [item["timestamp"] for item in body["signals"]] == [
        day2.isoformat(),
        (day2 + timedelta(hours=1)).isoformat(),
    ]

def test_strategy_run_resume_route_continues_from_checkpoint_without_duplicates(monkeypatch, tmp_path):
    ledger_db = str(tmp_path / "resume-api.db")
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)
    start = datetime(2026, 9, 2, 10, 0)
    points = [
        payload(gap=10, timestamp=start),
        payload(gap=8, timestamp=start + timedelta(hours=1)),
        payload(gap=4, timestamp=start + timedelta(hours=2)),
    ]
    fingerprint = provenance_hash({"input_identity": "cash_future_points:v1", "points": points})
    config = CashFutureStrategyConfig(
        initial_capital=10_000_000,
        start_date=start.date(),
        end_date=start.date(),
        checkpoint_interval=2,
    )
    ledger = BacktestLedger(ledger_db)
    try:
        run_cash_future_strategy(
            tuple(CashFutureHistoryPoint(**{**item, "timestamp": datetime.fromisoformat(item["timestamp"]), "expiry_date": date.fromisoformat(item["expiry_date"])}) for item in points[:2]),
            _build_builder_strategy(StrategyRunRequest(
                strategy_id="gap_threshold",
                initial_capital=10_000_000,
                start_date=start.date(),
                end_date=start.date(),
                target=5.0,
                points=points[:2],
            )),
            strategy_id="gap_threshold",
            strategy_version="1",
            config=config,
            ledger=ledger,
            run_id="cash-future-resume-api",
            strategy_hash=_gap_threshold_implementation_hash(),
            strategy_config_hash=provenance_hash({
                "cash_side": "BUY", "future_side": "SELL", "stop_loss": None, "target": 5.0
            }),
            data_source_fingerprint=fingerprint,
        )
    finally:
        ledger.close()

    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run/cash-future-resume-api/resume",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
            "initial_capital": 10_000_000,
            "target": 5.0,
            "checkpoint_interval": 2,
            "points": points,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["signal_count"] == 2
    assert body["trade_count"] == 1
    assert body["net_profit"] == 600.0
    assert len(body["signals"]) == 2
    assert len(body["trades"]) == 1


def test_strategy_run_resume_route_rejects_mismatched_data(monkeypatch, tmp_path):
    ledger_db = str(tmp_path / "resume-mismatch.db")
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)
    start = datetime(2026, 9, 2, 10, 0)
    points = [payload(gap=10, timestamp=start), payload(gap=8, timestamp=start + timedelta(hours=1))]
    fingerprint = provenance_hash({"input_identity": "cash_future_points:v1", "points": points})
    ledger = BacktestLedger(ledger_db)
    try:
        run_cash_future_strategy(
            tuple(CashFutureHistoryPoint(**{**item, "timestamp": datetime.fromisoformat(item["timestamp"]), "expiry_date": date.fromisoformat(item["expiry_date"])}) for item in points),
            lambda current, history: "NONE",
            strategy_id="gap_threshold",
            strategy_version="1",
            config=CashFutureStrategyConfig(initial_capital=10_000_000, checkpoint_interval=1),
            ledger=ledger,
            run_id="resume-mismatch",
            strategy_hash=_gap_threshold_implementation_hash(),
            strategy_config_hash=provenance_hash({"cash_side": "BUY", "future_side": "SELL", "stop_loss": None, "target": None}),
            data_source_fingerprint=fingerprint,
        )
    finally:
        ledger.close()

    changed = [dict(item) for item in points]
    changed[1]["gap"] = 7.0
    response = client().post(
        "/api/v1/backtesting/cash-future/strategy-run/resume-mismatch/resume",
        json={
            "strategy_id": "gap_threshold",
            "strategy_version": "1",
            "initial_capital": 10_000_000,
            "checkpoint_interval": 1,
            "points": changed,
        },
    )
    assert response.status_code == 422
    assert "data source fingerprint" in response.json()["detail"]


def test_strategy_run_resume_route_rejects_mismatched_strategy_configuration(monkeypatch, tmp_path):
    ledger_db = str(tmp_path / "resume-config-mismatch.db")
    monkeypatch.setattr(settings, "BACKTEST_LEDGER_DB", ledger_db)
    start = datetime(2026, 9, 2, 10, 0)
    points = [payload(gap=10, timestamp=start), payload(gap=8, timestamp=start + timedelta(hours=1))]
    fingerprint = provenance_hash({"input_identity": "cash_future_points:v1", "points": points})
    stored_config_hash = provenance_hash({"cash_side": "BUY", "future_side": "SELL", "stop_loss": None, "target": 5.0})
    ledger = BacktestLedger(ledger_db)
    try:
        run_cash_future_strategy(tuple(CashFutureHistoryPoint(**{**item, "timestamp": datetime.fromisoformat(item["timestamp"]), "expiry_date": date.fromisoformat(item["expiry_date"])}) for item in points), _build_builder_strategy(StrategyRunRequest(strategy_id="gap_threshold", initial_capital=10_000_000, target=5.0, points=points)), strategy_id="gap_threshold", strategy_version="1", config=CashFutureStrategyConfig(initial_capital=10_000_000, checkpoint_interval=1), ledger=ledger, run_id="resume-config-mismatch", strategy_hash=_gap_threshold_implementation_hash(), strategy_config_hash=stored_config_hash, data_source_fingerprint=fingerprint)
    finally:
        ledger.close()
    response = client().post("/api/v1/backtesting/cash-future/strategy-run/resume-config-mismatch/resume", json={"strategy_id": "gap_threshold", "strategy_version": "1", "initial_capital": 10_000_000, "checkpoint_interval": 1, "points": points})
    assert response.status_code == 422
    assert "strategy configuration hash mismatch" in response.json()["detail"]
