from app.backtesting.arbitrage_backtest_suite import build_strategy_adapter
from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.historical_arbitrage_backtest import run_historical_arbitrage
from app.backtesting.result_ledger import BacktestResultLedger


def _option(ts, strike, cb, ca, pb, pa):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": 20261231, "strike": strike,
            "call_bid": cb, "call_ask": ca, "put_bid": pb, "put_ask": pa}


def test_unified_box_replay_persists_trade_and_graph_ready_payoff():
    ledger = BacktestResultLedger()
    spec = BacktestRunSpec(
        run_id="unified-box",
        strategy_id="box-spread",
        strategy_version="v1",
        instrument="NFO:ABC",
        start_ns=1,
        end_ns=3,
        resolution=BacktestResolution("s", "historical", 1, 3),
        parameters={"direction": "LONG", "fees_per_unit": 1.0},
    )
    events = [
        {"timestamp_ns": 1, "data_resolution": "s", "low": _option(1, 100, 6, 4, 5, 3),
         "high": _option(1, 110, 2, 3, 2, 2)},
        {"timestamp_ns": 2, "data_resolution": "s", "low": _option(2, 100, 6, 7, 5, 6),
         "high": _option(2, 110, 2, 2, 1, 2)},
        {"timestamp_ns": 3, "data_resolution": "s", "low": _option(3, 100, 7, 8, 6, 7),
         "high": _option(3, 110, 1, 1, 1, 2)},
    ]
    result = run_historical_arbitrage(ledger, spec, events, payoff_prices=(90, 100, 110, 120))
    assert result.completed_trades == 1
    assert result.payoff is not None
    assert len(result.payoff.prices) == 4
    assert ledger.trades("unified-box")[0]["net_pnl"] == 7.0
    assert ledger.events("unified-box")[0]["event_type"] == "PAYOFF_SNAPSHOT"
    assert ledger.run("unified-box")["status"] == "COMPLETED"
    ledger.close()


def test_registry_adapter_isolated_from_other_runs():
    first = build_strategy_adapter("cash-future", {"direction": "LONG_CASH_SHORT_FUTURE"})
    second = build_strategy_adapter("cash-future", {"direction": "SHORT_CASH_LONG_FUTURE"})
    assert first is not second
    assert first.direction != second.direction
