from app.backtesting.arbitrage_payoff import build_box_payoff
from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.historical_arbitrage_service import HistoricalArbitrageBacktestService
from app.backtesting.result_ledger import BacktestResultLedger


def _writer(tmp_path):
    ledger = BacktestResultLedger(tmp_path / "results.db")
    spec = BacktestRunSpec(
        run_id="payoff-auto", strategy_id="box-spread", strategy_version="v1",
        instrument="NFO:ABC", start_ns=1, end_ns=3,
        resolution=BacktestResolution("s", "test", 1, 3), parameters={},
    )
    return ledger, BacktestRunWriter(ledger, spec)


def option(ts, strike, cb, ca, pb, pa):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": 20261231, "strike": strike,
            "call_bid": cb, "call_ask": ca, "put_bid": pb, "put_ask": pa, "lot_size": 1}


def test_registered_box_strategy_auto_builds_payoff_from_first_entry_quote(tmp_path):
    ledger, writer = _writer(tmp_path)
    service = HistoricalArbitrageBacktestService(writer)
    entry = {"timestamp_ns": 1, "low": option(1, 100, 6, 4, 5, 3),
             "high": option(1, 110, 2, 3, 2, 2)}
    later = {"timestamp_ns": 3, "low": option(3, 100, 7, 8, 6, 7),
             "high": option(3, 110, 1, 1, 1, 2)}

    result = service.run_strategy(
        "box-spread", (entry, later),
        payoff_prices=(90.0, 100.0, 105.0, 110.0, 120.0),
    )

    expected = build_box_payoff(entry).legs
    assert result.payoff is not None
    assert result.completed_trades == 1
    assert result.payoff.pnl[2] == result.payoff.pnl[3]
    assert len(expected) == 4
    assert len(ledger.events("payoff-auto")) == 3
