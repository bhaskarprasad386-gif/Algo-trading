from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.historical_arbitrage_service import HistoricalArbitrageBacktestService
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_catalog_replay import CatalogReplayLeg, HistoricalCatalogEventReplay
from app.backtesting.result_ledger import BacktestResultLedger
import json


def _writer(tmp_path, run_id, strategy_id):
    ledger = BacktestResultLedger(tmp_path / f"{run_id}.db")
    spec = BacktestRunSpec(
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version="v1",
        instrument="ABC",
        start_ns=1,
        end_ns=2,
        resolution=BacktestResolution("s", "test", 1, 2),
        parameters={},
    )
    return ledger, BacktestRunWriter(ledger, spec)


def test_catalog_replay_joins_only_exact_complete_timestamps():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "NSE:ABC", "1m", 1, {"bid": 99, "ask": 100}),
        HistoricalRecord("test", "NSE:ABC", "1m", 2, {"bid": 100, "ask": 101}),
        HistoricalRecord("test", "NFO:FUT", "1m", 1, {"bid": 109, "ask": 110}),
        HistoricalRecord("test", "NFO:FUT", "1m", 3, {"bid": 111, "ask": 112}),
    ])
    events = list(HistoricalCatalogEventReplay(catalog).events((CatalogReplayLeg("cash_future", "test", "NSE:ABC", "1m"), CatalogReplayLeg("future", "test", "NFO:FUT", "1m")), start_ns=1, end_ns=3))
    assert [event["timestamp_ns"] for event in events] == [1]
    assert events[0]["cash_future"]["ask"] == 100
    assert events[0]["future"]["bid"] == 109


def test_catalog_replay_never_fabricates_missing_leg():
    catalog = HistoricalCatalog()
    catalog.ingest([HistoricalRecord("test", "A", "ms", 10, {"price": 10}), HistoricalRecord("test", "A", "ms", 20, {"price": 20}), HistoricalRecord("test", "B", "ms", 10, {"price": 30})])
    events = list(HistoricalCatalogEventReplay(catalog).events((CatalogReplayLeg("a", "test", "A", "ms"), CatalogReplayLeg("b", "test", "B", "ms")), start_ns=10, end_ns=20))
    assert [event["timestamp_ns"] for event in events] == [10]
    assert all(event["timestamp_ns"] != 20 for event in events)


def test_catalog_replay_rejects_duplicate_timestamp_per_leg():
    catalog = HistoricalCatalog()
    catalog.ingest([HistoricalRecord("test", "A", "ms", 10, {"price": 10}, sequence=1), HistoricalRecord("test", "A", "ms", 10, {"price": 11}, sequence=2)])
    try:
        list(HistoricalCatalogEventReplay(catalog).events((CatalogReplayLeg("a", "test", "A", "ms"),), start_ns=10, end_ns=10))
    except ValueError as exc:
        assert "multiple catalog records" in str(exc)
    else:
        raise AssertionError("duplicate timestamp must be rejected")


def test_catalog_to_ledger_e2e_cash_future(tmp_path):
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("angel", "ABC-SPOT", "1m", 1, {"timestamp_ns": 1, "underlying": "ABC", "spot_bid": 99, "spot_ask": 100, "future_bid": 110, "future_ask": 111, "expiry": 20260924}),
        HistoricalRecord("angel", "ABC-SPOT", "1m", 2, {"timestamp_ns": 2, "underlying": "ABC", "spot_bid": 120, "spot_ask": 121, "future_bid": 90, "future_ask": 91, "expiry": 20260924}),
    ])
    ledger, writer = _writer(tmp_path, "catalog-cash-future", "cash-future")
    result = HistoricalArbitrageBacktestService(writer).run_catalog_strategy("cash-future", catalog, (CatalogReplayLeg("cash_future", "angel", "ABC-SPOT", "1m"),), payoff_prices=(90, 100, 120))
    assert result.completed_trades == 1
    assert result.unresolved_trades == 0
    assert result.payoff is not None and len(result.payoff.legs) == 2
    assert len(ledger.trades("catalog-cash-future")) == 1
    events = ledger.events("catalog-cash-future")
    assert [event["event_type"] for event in events][-1] == "PAYOFF_SNAPSHOT"
    assert {event["event_type"] for event in events} >= {"POSITION_OPENED", "POSITION_CLOSED", "PAYOFF_SNAPSHOT"}
    assert ledger.run("catalog-cash-future")["status"] == "COMPLETED"


def test_catalog_to_ledger_e2e_synthetic_cash_future(tmp_path):
    catalog = HistoricalCatalog()
    option_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 100, "call_bid": 14, "call_ask": 15, "put_bid": 5, "put_ask": 6, "lot_size": 1}
    future_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "bid": 120, "ask": 121, "lot_size": 1}
    option_2 = {**option_1, "timestamp_ns": 2, "call_bid": 25, "call_ask": 26, "put_bid": 2, "put_ask": 3}
    future_2 = {**future_1, "timestamp_ns": 2, "bid": 90, "ask": 91}
    catalog.ingest([HistoricalRecord("angel", "ABC-OPT", "1m", 1, option_1), HistoricalRecord("angel", "ABC-OPT", "1m", 2, option_2), HistoricalRecord("angel", "ABC-FUT", "1m", 1, future_1), HistoricalRecord("angel", "ABC-FUT", "1m", 2, future_2)])
    ledger, writer = _writer(tmp_path, "catalog-synthetic", "synthetic-cash-carry")
    result = HistoricalArbitrageBacktestService(writer).run_catalog_strategy("synthetic-cash-carry", catalog, (CatalogReplayLeg("option", "angel", "ABC-OPT", "1m"), CatalogReplayLeg("future", "angel", "ABC-FUT", "1m")), payoff_prices=(90, 100, 110))
    assert result.completed_trades == 1
    assert result.unresolved_trades == 0
    assert result.payoff is not None
    assert {leg.kind for leg in result.payoff.legs} == {"CALL", "PUT", "FUTURE"}
    assert len(ledger.trades("catalog-synthetic")) == 1
    assert ledger.run("catalog-synthetic")["status"] == "COMPLETED"


def test_catalog_to_ledger_e2e_calendar_preserves_expiries(tmp_path):
    catalog = HistoricalCatalog()
    near_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "bid": 100, "ask": 100, "lot_size": 1, "strike": 25000, "option_type": "CALL"}
    far_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20261029, "bid": 120, "ask": 121, "lot_size": 1, "strike": 25000, "option_type": "CALL"}
    near_2 = {**near_1, "timestamp_ns": 2, "bid": 130, "ask": 131}
    far_2 = {**far_1, "timestamp_ns": 2, "bid": 80, "ask": 90}
    catalog.ingest([HistoricalRecord("angel", "ABC-NEAR", "1m", 1, near_1), HistoricalRecord("angel", "ABC-NEAR", "1m", 2, near_2), HistoricalRecord("angel", "ABC-FAR", "1m", 1, far_1), HistoricalRecord("angel", "ABC-FAR", "1m", 2, far_2)])
    ledger, writer = _writer(tmp_path, "catalog-calendar", "calendar-spread")
    result = HistoricalArbitrageBacktestService(writer).run_catalog_strategy("calendar-spread", catalog, (CatalogReplayLeg("near", "angel", "ABC-NEAR", "1m"), CatalogReplayLeg("far", "angel", "ABC-FAR", "1m")), payoff_prices=(24900, 25000, 25100))
    assert result.completed_trades == 1
    assert result.unresolved_trades == 0
    assert result.payoff is not None and len(result.payoff.legs) == 2
    trades = ledger.trades("catalog-calendar")
    assert len(trades) == 1
    metadata = json.loads(trades[0]["metadata_json"] or "{}")
    assert metadata.get("near_expiry") == 20260924
    assert metadata.get("far_expiry") == 20261029
    assert ledger.run("catalog-calendar")["status"] == "COMPLETED"


def test_catalog_to_ledger_e2e_box_spread(tmp_path):
    catalog = HistoricalCatalog()
    low_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 25000, "call_bid": 20, "call_ask": 30, "put_bid": 20, "put_ask": 30, "lot_size": 1}
    high_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 25100, "call_bid": 10, "call_ask": 20, "put_bid": 10, "put_ask": 20, "lot_size": 1}
    low_2 = {**low_1, "timestamp_ns": 2, "call_bid": 80, "call_ask": 90, "put_bid": 70, "put_ask": 80}
    high_2 = {**high_1, "timestamp_ns": 2, "call_bid": 5, "call_ask": 20, "put_bid": 5, "put_ask": 20}
    catalog.ingest([HistoricalRecord("angel", "ABC-LOW", "1m", 1, low_1), HistoricalRecord("angel", "ABC-LOW", "1m", 2, low_2), HistoricalRecord("angel", "ABC-HIGH", "1m", 1, high_1), HistoricalRecord("angel", "ABC-HIGH", "1m", 2, high_2)])
    ledger, writer = _writer(tmp_path, "catalog-box", "box-spread")
    result = HistoricalArbitrageBacktestService(writer).run_catalog_strategy("box-spread", catalog, (CatalogReplayLeg("low", "angel", "ABC-LOW", "1m"), CatalogReplayLeg("high", "angel", "ABC-HIGH", "1m")), payoff_prices=(24900, 25000, 25100))
    assert result.completed_trades == 1
    assert result.unresolved_trades == 0
    assert result.payoff is not None and len(result.payoff.legs) == 4
    assert len(ledger.trades("catalog-box")) == 1
    assert ledger.run("catalog-box")["status"] == "COMPLETED"


def test_catalog_e2e_missing_exit_never_creates_trade_or_pnl(tmp_path):
    catalog = HistoricalCatalog()
    catalog.ingest([HistoricalRecord("angel", "ABC-SPOT", "1m", 1, {"timestamp_ns": 1, "underlying": "ABC", "spot_bid": 99, "spot_ask": 100, "future_bid": 110, "future_ask": 111, "expiry": 20260924})])
    ledger, writer = _writer(tmp_path, "catalog-unresolved", "cash-future")
    result = HistoricalArbitrageBacktestService(writer).run_catalog_strategy("cash-future", catalog, (CatalogReplayLeg("cash_future", "angel", "ABC-SPOT", "1m"),), payoff_prices=(90, 100, 120))
    assert result.completed_trades == 0
    assert result.unresolved_trades == 1
    assert result.realized_pnl == 0
    assert ledger.trades("catalog-unresolved") == []
    assert ledger.run("catalog-unresolved")["status"] == "COMPLETED"
