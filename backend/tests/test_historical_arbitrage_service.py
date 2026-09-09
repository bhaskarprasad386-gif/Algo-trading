from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.historical_arbitrage_service import HistoricalArbitrageBacktestService
from app.backtesting.historical_arbitrage_runner import ExitExecution, OpenPosition
from app.backtesting.result_ledger import BacktestResultLedger
from app.execution.payoff import PayoffLeg


def _writer(tmp_path, run_id="unified-1", strategy_id="calendar"):
    ledger = BacktestResultLedger(tmp_path / f"{run_id}.db")
    spec = BacktestRunSpec(
        run_id=run_id, strategy_id=strategy_id, strategy_version="v1",
        instrument="NFO:ABC", start_ns=1, end_ns=3,
        resolution=BacktestResolution("s", "test", 1, 3), parameters={},
    )
    return ledger, BacktestRunWriter(ledger, spec)


def test_service_persists_trade_and_payoff_without_fabricating_exit(tmp_path):
    ledger, writer = _writer(tmp_path)
    service = HistoricalArbitrageBacktestService(writer)

    def entry(event):
        if event["timestamp_ns"] == 1:
            return (OpenPosition("t1", 1, "NFO:ABC", "LONG", 1, 2.0,
                                 contract="CALENDAR:20260924:20261029", expiry="20260924",
                                 leg="CALENDAR", data_resolution="1s"),)
        return ()

    def exit(position, event):
        if event["timestamp_ns"] == 3:
            return ExitExecution(3, 5.0, 3.0, fees=0.5, slippage=0.25)
        return None

    result = service.run(
        ({"timestamp_ns": 1}, {"timestamp_ns": 2}, {"timestamp_ns": 3}),
        entry_selector=entry, exit_selector=exit,
        payoff_legs=(PayoffLeg("FUTURE", "BUY", None, 100.0, 1),),
        payoff_prices=(99.0, 100.0, 101.0),
    )
    assert result.completed_trades == 1
    assert result.unresolved_trades == 0
    assert result.realized_pnl == 2.25
    assert result.payoff is not None
    assert len(ledger.trades("unified-1")) == 1
    assert len(ledger.events("unified-1")) == 3
    assert ledger.run("unified-1")["status"] == "COMPLETED"


def test_run_strategy_routes_through_registered_adapter(tmp_path):
    ledger, writer = _writer(tmp_path)
    service = HistoricalArbitrageBacktestService(writer)

    result = service.run_strategy("box-spread", ())

    assert result.run_id == "unified-1"
    assert result.completed_trades == 0
    assert result.unresolved_trades == 0
    assert ledger.run("unified-1")["status"] == "COMPLETED"


def test_run_strategy_rejects_unknown_strategy(tmp_path):
    _, writer = _writer(tmp_path)
    service = HistoricalArbitrageBacktestService(writer)

    try:
        service.run_strategy("not-a-strategy", ())
    except ValueError as exc:
        assert "unsupported arbitrage strategy" in str(exc)
    else:
        raise AssertionError("unknown strategy must be rejected")


def test_run_strategy_builds_box_payoff_before_completion(tmp_path):
    ledger, writer = _writer(tmp_path, "box-e2e", "box-spread")
    service = HistoricalArbitrageBacktestService(writer)
    entry = {
        "timestamp_ns": 1, "data_resolution": "1s",
        "low": {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 25000,
                "call_bid": 20, "call_ask": 30, "put_bid": 20, "put_ask": 30, "lot_size": 1},
        "high": {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 25100,
                 "call_bid": 10, "call_ask": 20, "put_bid": 10, "put_ask": 20, "lot_size": 1},
    }
    exit_event = {
        "timestamp_ns": 2,
        "low": {**entry["low"], "call_bid": 80, "call_ask": 90, "put_bid": 70, "put_ask": 80},
        "high": {**entry["high"], "call_bid": 5, "call_ask": 20, "put_bid": 5, "put_ask": 20},
    }
    result = service.run_strategy("box-spread", [entry, exit_event], payoff_prices=(24900, 25000, 25100))
    assert result.completed_trades == 1
    assert result.payoff is not None and len(result.payoff.prices) == 3
    assert len(result.payoff.legs) == 4
    assert ledger.run("box-e2e")["status"] == "COMPLETED"
    assert ledger.events("box-e2e")[-1]["event_type"] == "PAYOFF_SNAPSHOT"


def test_run_strategy_builds_synthetic_payoff(tmp_path):
    ledger, writer = _writer(tmp_path, "synthetic-e2e", "synthetic-cash-carry")
    service = HistoricalArbitrageBacktestService(writer)
    option = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 100,
              "call_bid": 14, "call_ask": 15, "put_bid": 5, "put_ask": 6, "lot_size": 1}
    future = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "bid": 120, "ask": 121, "lot_size": 1}
    later_option = {**option, "timestamp_ns": 2, "call_bid": 25, "call_ask": 26, "put_bid": 2, "put_ask": 3}
    later_future = {**future, "timestamp_ns": 2, "bid": 90, "ask": 91}
    result = service.run_strategy("synthetic-cash-carry",
                                  [{"timestamp_ns": 1, "option": option, "future": future},
                                   {"timestamp_ns": 2, "option": later_option, "future": later_future}],
                                  payoff_prices=(90, 100, 110))
    assert result.completed_trades == 1
    assert result.payoff is not None and len(result.payoff.legs) == 3
    assert {leg.kind for leg in result.payoff.legs} == {"CALL", "PUT", "FUTURE"}
    assert ledger.run("synthetic-e2e")["status"] == "COMPLETED"


def test_run_strategy_builds_cash_future_payoff(tmp_path):
    ledger, writer = _writer(tmp_path, "cash-future-e2e", "cash-future")
    service = HistoricalArbitrageBacktestService(writer)
    first = {"timestamp_ns": 1, "cash_future": {"timestamp_ns": 1, "underlying": "ABC",
             "spot_bid": 99, "spot_ask": 100, "future_bid": 110, "future_ask": 111, "expiry": 20260924}}
    second = {"timestamp_ns": 2, "cash_future": {"timestamp_ns": 2, "underlying": "ABC",
              "spot_bid": 120, "spot_ask": 121, "future_bid": 90, "future_ask": 91, "expiry": 20260924}}
    result = service.run_strategy("cash-future", [first, second], payoff_prices=(90, 100, 120))
    assert result.completed_trades == 1
    assert result.payoff is not None and len(result.payoff.legs) == 2
    assert {leg.kind for leg in result.payoff.legs} == {"SPOT", "FUTURE"}
    assert ledger.run("cash-future-e2e")["status"] == "COMPLETED"


def test_run_strategy_builds_calendar_payoff_with_both_expiries(tmp_path):
    ledger, writer = _writer(tmp_path, "calendar-e2e", "calendar-spread")
    service = HistoricalArbitrageBacktestService(writer)
    near = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924,
            "bid": 100, "ask": 100, "lot_size": 1, "strike": 25000, "option_type": "CALL"}
    far = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20261029,
           "bid": 120, "ask": 121, "lot_size": 1, "strike": 25000, "option_type": "CALL"}
    later_near = {**near, "timestamp_ns": 2, "bid": 130, "ask": 131}
    later_far = {**far, "timestamp_ns": 2, "bid": 80, "ask": 90}
    result = service.run_strategy("calendar-spread",
                                  [{"timestamp_ns": 1, "near": near, "far": far},
                                   {"timestamp_ns": 2, "near": later_near, "far": later_far}],
                                  payoff_prices=(24900, 25000, 25100))
    assert result.completed_trades == 1
    assert result.payoff is not None and len(result.payoff.legs) == 2
    assert {leg.kind for leg in result.payoff.legs} == {"CALL"}
    assert {leg.side for leg in result.payoff.legs} == {"BUY", "SELL"}
    assert {leg.entry_price for leg in result.payoff.legs} == {100, 120}
    assert ledger.run("calendar-e2e")["status"] == "COMPLETED"
