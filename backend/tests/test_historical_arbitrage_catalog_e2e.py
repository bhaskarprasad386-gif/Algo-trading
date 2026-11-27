import pytest

from app.backtesting.backtest_run import BacktestResolution, BacktestRunSpec
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.historical_arbitrage_service import HistoricalArbitrageBacktestService
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_catalog_replay import CatalogReplayLeg
from app.backtesting.result_ledger import BacktestResultLedger


def test_retired_cash_future_catalog_replay_is_rejected_before_ledger_execution(tmp_path):
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

    with pytest.raises(ValueError, match="unsupported arbitrage strategy: cash-future"):
        HistoricalArbitrageBacktestService(writer).run_catalog_strategy(
            "cash-future", catalog,
            (CatalogReplayLeg("cash_future", "historical", "NSE:ABC", "1m"),),
            payoff_prices=(90, 100, 120),
        )

    assert ledger.trades("catalog-cf-e2e") == []
    assert ledger.events("catalog-cf-e2e") == []
    assert ledger.run("catalog-cf-e2e")["status"] == "CREATED"
