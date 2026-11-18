from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.backtest_run import BacktestResolution, BacktestRunSpec
from app.backtesting.historical_arbitrage_service import HistoricalArbitrageBacktestService
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_catalog_replay import CatalogReplayLeg
from app.backtesting.result_ledger import BacktestResultLedger


def test_catalog_cash_future_replay_reaches_ledger_and_pnl(tmp_path):
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("historical", "NSE:ABC", "1m", 1, {
            "timestamp_ns": 1, "underlying": "ABC", "spot_bid": 99, "spot_ask": 100,
            "future_bid": 110, "future_ask": 111, "expiry": 20260924, "lot_size": 1,
        }),
        HistoricalRecord("historical", "NSE:ABC", "1m", 2, {
            "timestamp_ns": 2, "underlying": "ABC", "spot_bid": 120, "spot_ask": 121,
            "future_bid": 90, "future_ask": 91, "expiry": 20260924, "lot_size": 1,
        }),
    ])

    ledger = BacktestResultLedger(tmp_path / "catalog-e2e.db")
    spec = BacktestRunSpec(
        run_id="catalog-cf-e2e", strategy_id="cash-future", strategy_version="v1",
        instrument="ABC", start_ns=1, end_ns=2,
        resolution=BacktestResolution("m", "historical", 1, 2), parameters={},
    )
    writer = BacktestRunWriter(ledger, spec)
    result = HistoricalArbitrageBacktestService(writer).run_catalog_strategy(
        "cash-future", catalog,
        (CatalogReplayLeg("cash_future", "historical", "NSE:ABC", "1m"),),
        payoff_prices=(90, 100, 120),
    )

    assert result.completed_trades == 1
    assert result.unresolved_trades == 0
    assert result.realized_pnl > 0
    assert result.payoff is not None
    assert ledger.run("catalog-cf-e2e")["status"] == "COMPLETED"
    assert len(ledger.trades("catalog-cf-e2e")) == 1
    assert ledger.events("catalog-cf-e2e")[-1]["event_type"] == "PAYOFF_SNAPSHOT"
