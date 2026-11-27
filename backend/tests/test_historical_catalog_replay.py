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


def test_catalog_replay_preserves_exact_leg_identity():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "NSE:ABC", "1m", 1, {"bid": 99, "ask": 100}, sequence=17),
        HistoricalRecord("test", "NFO:FUT-20260924", "1m", 1, {"bid": 109, "ask": 110}, sequence=29),
    ])
    legs = (
        CatalogReplayLeg("cash_future", "test", "NSE:ABC", "1m"),
        CatalogReplayLeg("future", "test", "NFO:FUT-20260924", "1m"),
    )
    event = next(HistoricalCatalogEventReplay(catalog).events(legs, start_ns=1, end_ns=1))
    assert event["__replay_legs__"]["cash_future"] == {
        "source": "test",
        "instrument": "NSE:ABC",
        "timeframe": "1m",
        "sequence": 17,
    }
    assert event["__replay_legs__"]["future"] == {
        "source": "test",
        "instrument": "NFO:FUT-20260924",
        "timeframe": "1m",
        "sequence": 29,
    }


def test_catalog_replay_identity_metadata_does_not_mutate_payload():
    payload = {"bid": 99, "ask": 100, "__replay_legs__": "payload-owned"}
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "NSE:ABC", "1m", 1, payload),
        HistoricalRecord("test", "NFO:FUT", "1m", 1, {"bid": 109, "ask": 110}),
    ])
    legs = (
        CatalogReplayLeg("cash", "test", "NSE:ABC", "1m"),
        CatalogReplayLeg("future", "test", "NFO:FUT", "1m"),
    )
    event = next(HistoricalCatalogEventReplay(catalog).events(legs, start_ns=1, end_ns=1))
    assert event["cash"]["__replay_legs__"] == "payload-owned"
    assert event["__replay_legs__"]["cash"]["instrument"] == "NSE:ABC"


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


def test_catalog_replay_reports_mixed_timeframes_explicitly():
    catalog = HistoricalCatalog()
    catalog.ingest([
        HistoricalRecord("test", "A", "1m", 10, {"price": 10}),
        HistoricalRecord("test", "B", "1s", 10, {"price": 20}),
    ])
    events = list(HistoricalCatalogEventReplay(catalog).events((
        CatalogReplayLeg("minute", "test", "A", "1m"),
        CatalogReplayLeg("second", "test", "B", "1s"),
    ), start_ns=10, end_ns=10))
    assert events[0]["data_resolution"] == "mixed"


def test_catalog_to_ledger_e2e_box_spread(tmp_path):
    catalog = HistoricalCatalog()
    low_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 25000, "call_bid": 20, "call_ask": 30, "put_bid": 20, "put_ask": 30, "lot_size": 1}
    high_1 = {"timestamp_ns": 1, "underlying": "ABC", "expiry": 20260924, "strike": 25100, "call_bid": 10, "call_ask": 20, "put_bid": 10, "put_ask": 20, "lot_size": 1}
    low_2 = {**low_1, "timestamp_ns": 2, "call_bid": 60, "call_ask": 61, "put_bid": 60, "put_ask": 61}
    high_2 = {**high_1, "timestamp_ns": 2, "call_bid": 3, "call_ask": 3.5, "put_bid": 110, "put_ask": 110.5}
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


def test_live_one_second_box_records_replay_only_on_exact_shared_timestamp():
    catalog = HistoricalCatalog()
    low = {"timestamp_ns": 1_000_000_000, "underlying": "ABC", "expiry": 20260924, "strike": 25000,
           "call_bid": 20, "call_ask": 21, "put_bid": 20, "put_ask": 21, "lot_size": 1}
    high = {"timestamp_ns": 1_000_000_000, "underlying": "ABC", "expiry": 20260924, "strike": 25100,
            "call_bid": 10, "call_ask": 11, "put_bid": 10, "put_ask": 11, "lot_size": 1}
    catalog.ingest([
        HistoricalRecord("angelone-live-synthetic", "ABC-LOW|101", "1s", 1_000_000_000, low),
        HistoricalRecord("angelone-live-synthetic", "ABC-HIGH|102", "1s", 1_000_000_000, high),
        HistoricalRecord("angelone-live-synthetic", "ABC-HIGH|102", "1s", 2_000_000_000, {**high, "timestamp_ns": 2_000_000_000}),
    ])
    events = list(HistoricalCatalogEventReplay(catalog).events((
        CatalogReplayLeg("low", "angelone-live-synthetic", "ABC-LOW|101", "1s"),
        CatalogReplayLeg("high", "angelone-live-synthetic", "ABC-HIGH|102", "1s"),
    ), start_ns=1_000_000_000, end_ns=2_000_000_000))
    assert [event["timestamp_ns"] for event in events] == [1_000_000_000]
    assert events[0]["data_resolution"] == "1s"
    assert events[0]["low"]["call_ask"] == 21
    assert events[0]["high"]["call_bid"] == 10
