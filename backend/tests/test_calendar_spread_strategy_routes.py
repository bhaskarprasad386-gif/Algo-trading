from datetime import date, datetime, timezone

import pytest

from app.backtesting.calendar_spread_strategy_routes import (
    CalendarSpreadPointRequest,
    CalendarSpreadRunRequest,
    strategy_run,
)


def point(ts: str, *, near_bid=99.0, near_ask=100.0, far_bid=103.0, far_ask=104.0):
    return CalendarSpreadPointRequest(
        timestamp=datetime.fromisoformat(ts),
        underlying="SBIN",
        near_expiry=20260930,
        far_expiry=20261029,
        near_bid=near_bid,
        near_ask=near_ask,
        far_bid=far_bid,
        far_ask=far_ask,
        lot_size=100,
        strike=850.0,
        option_type="CALL",
    )


def test_calendar_spread_request_validates_near_far_and_direction():
    request = CalendarSpreadRunRequest(
        points=[point("2026-09-24T09:15:00+00:00")],
        direction="LONG_NEAR_SHORT_FAR",
    )
    assert request.strategy_id == "calendar-spread"

    with pytest.raises(ValueError):
        CalendarSpreadRunRequest(
            points=[point("2026-09-24T09:15:00+00:00")],
            direction="INVALID",
        )


def test_calendar_spread_strategy_run_reconciles_executable_edge_and_fees(tmp_path, monkeypatch):
    from app.core.config import settings

    db = tmp_path / "calendar-results.sqlite"
    monkeypatch.setattr(settings, "BACKTEST_RESULT_LEDGER_DB", str(db))

    result = strategy_run(
        CalendarSpreadRunRequest(
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point(
                    "2026-09-24T09:15:01+00:00",
                    near_bid=102.0,
                    near_ask=103.0,
                    far_bid=98.0,
                    far_ask=99.0,
                ),
            ],
            fees_per_unit=0.5,
        )
    )

    assert result["status"] == "success"
    assert result["completed_trades"] == 1
    assert result["unresolved_trades"] == 0
    # Entry edge = 103 - 100 = 3; exit reverse edge = 102 - 99 = 3.
    # Gross = (3 + 3) * 100 = 600; two-leg fees = 0.5 * 2 * 100 = 100.
    assert result["net_profit"] == 500.0
    assert result["trade_count"] == 1
    assert result["trades"][0]["gross_pnl"] == 600.0
    assert result["trades"][0]["fees"] == 100.0


def test_calendar_spread_strategy_run_respects_exact_timestamp_window(tmp_path, monkeypatch):
    from app.core.config import settings

    db = tmp_path / "calendar-window.sqlite"
    monkeypatch.setattr(settings, "BACKTEST_RESULT_LEDGER_DB", str(db))

    result = strategy_run(
        CalendarSpreadRunRequest(
            start_timestamp=datetime(2026, 9, 24, 9, 15, 1, tzinfo=timezone.utc),
            end_timestamp=datetime(2026, 9, 24, 9, 15, 2, tzinfo=timezone.utc),
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point("2026-09-24T09:15:01+00:00"),
                point(
                    "2026-09-24T09:15:02+00:00",
                    near_bid=102.0,
                    near_ask=103.0,
                    far_bid=98.0,
                    far_ask=99.0,
                ),
                point("2026-09-24T09:15:03+00:00"),
            ],
        )
    )

    assert result["completed_trades"] == 1
    assert result["start_timestamp"].startswith("2026-09-24T09:15:01")
    assert result["end_timestamp"].startswith("2026-09-24T09:15:02")


def test_calendar_spread_replay_exposes_only_source_supported_timeframes():
    from app.backtesting.calendar_spread_strategy_routes import calendar_spread_replay

    result = calendar_spread_replay(
        __import__("app.backtesting.calendar_spread_strategy_routes", fromlist=["CalendarSpreadReplayRequest"]).CalendarSpreadReplayRequest(
            trading_date=datetime(2026, 9, 24, tzinfo=timezone.utc).date(),
            underlying="SBIN",
            timeframe="1s",
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point("2026-09-24T09:15:01+00:00"),
                point("2026-09-24T09:15:02+00:00"),
            ],
        )
    )

    assert result["source_min_interval_seconds"] == 1.0
    assert result["available_replay_intervals"] == [
        "1s", "30s", "1m", "5m", "15m", "30m", "1h"
    ]
    assert result["count"] == 3


def test_calendar_spread_replay_minute_source_does_not_claim_1s():
    from app.backtesting.calendar_spread_strategy_routes import (
        CalendarSpreadReplayRequest,
        calendar_spread_replay,
    )

    result = calendar_spread_replay(
        CalendarSpreadReplayRequest(
            trading_date=datetime(2026, 9, 24, tzinfo=timezone.utc).date(),
            underlying="SBIN",
            timeframe="1m",
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point("2026-09-24T09:16:00+00:00"),
            ],
        )
    )

    assert result["source_min_interval_seconds"] == 60.0
    assert result["available_replay_intervals"] == [
        "1m", "5m", "15m", "30m", "1h"
    ]


def test_calendar_spread_historical_replay_reads_durable_catalog(tmp_path, monkeypatch):
    from app.backtesting.calendar_spread_strategy_routes import (
        CalendarSpreadHistoricalReplayRequest,
        calendar_spread_historical_replay,
    )
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
    from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord

    data_db = tmp_path / "data.sqlite"
    contract_db = tmp_path / "contracts.sqlite"
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_DATA_DB", str(data_db))
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_CONTRACT_DB", str(contract_db))

    snapshot = date(2026, 9, 24)
    contracts = ContractMasterCatalog(str(contract_db))
    contracts.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "SBIN30SEP2026FUT", "1001", date(2026, 9, 30), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("NFO", "SBIN29OCT2026FUT", "1002", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 150, snapshot),
    ])
    contracts.close()

    base = int(datetime(2026, 9, 24, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    data = HistoricalCatalog(str(data_db))
    data.ingest([
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1s", base, {"bid": 100, "ask": 101}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1s", base, {"bid": 104, "ask": 105}),
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1s", base + 1_000_000_000, {"bid": 101, "ask": 102}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1s", base + 1_000_000_000, {"bid": 105, "ask": 106}),
    ])
    data.close()

    request = CalendarSpreadHistoricalReplayRequest(
        underlying="SBIN",
        exchange="NFO",
        start_date=snapshot,
        end_date=snapshot,
        near_contract_month="2026-09",
        far_contract_month="2026-10",
        source_timeframe="1s",
        replay_timeframe="1s",
    )
    result = calendar_spread_historical_replay(request)

    assert result["count"] == 2
    assert result["source_min_interval_seconds"] == 1.0
    assert result["available_replay_intervals"] == ["1s", "30s", "1m", "5m", "15m", "30m", "1h"]
    assert result["series"][0]["near_expiry"] == "2026-09-30"
    assert result["series"][0]["far_expiry"] == "2026-10-29"

def test_calendar_spread_historical_replay_respects_exact_time_window(tmp_path, monkeypatch):
    from app.backtesting.calendar_spread_strategy_routes import (
        CalendarSpreadHistoricalReplayRequest,
        calendar_spread_historical_replay,
    )
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
    from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord

    data_db = tmp_path / "data-window.sqlite"
    contract_db = tmp_path / "contracts-window.sqlite"
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_DATA_DB", str(data_db))
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_CONTRACT_DB", str(contract_db))

    snapshot = date(2026, 9, 24)
    contracts = ContractMasterCatalog(str(contract_db))
    contracts.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "SBIN30SEP2026FUT", "1001", date(2026, 9, 30), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("NFO", "SBIN29OCT2026FUT", "1002", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 150, snapshot),
    ])
    contracts.close()

    base = int(datetime(2026, 9, 24, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    data = HistoricalCatalog(str(data_db))
    data.ingest([
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1s", base, {"bid": 100, "ask": 101}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1s", base, {"bid": 104, "ask": 105}),
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1s", base + 1_000_000_000, {"bid": 101, "ask": 102}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1s", base + 1_000_000_000, {"bid": 105, "ask": 106}),
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1s", base + 2_000_000_000, {"bid": 102, "ask": 103}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1s", base + 2_000_000_000, {"bid": 106, "ask": 107}),
    ])
    data.close()

    result = calendar_spread_historical_replay(CalendarSpreadHistoricalReplayRequest(
        underlying="SBIN",
        exchange="NFO",
        start_date=snapshot,
        end_date=snapshot,
        near_contract_month="2026-09",
        far_contract_month="2026-10",
        source_timeframe="1s",
        replay_timeframe="1s",
        start_timestamp=datetime(2026, 9, 24, 14, 45, 1, tzinfo=timezone.utc),
        end_timestamp=datetime(2026, 9, 24, 14, 45, 2, tzinfo=timezone.utc),
    ))

    assert result["count"] == 2
    assert result["series"][0]["timestamp"].startswith("2026-09-24T14:45:01")
    assert result["series"][-1]["timestamp"].startswith("2026-09-24T14:45:02")


def test_calendar_spread_historical_strategy_run_executes_catalog_data(tmp_path, monkeypatch):
    from app.backtesting.calendar_spread_strategy_routes import (
        CalendarSpreadHistoricalStrategyRunRequest,
        calendar_spread_historical_strategy_run,
    )
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
    from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord

    data_db = tmp_path / "strategy-data.sqlite"
    contract_db = tmp_path / "strategy-contracts.sqlite"
    result_db = tmp_path / "strategy-results.sqlite"
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_DATA_DB", str(data_db))
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_CONTRACT_DB", str(contract_db))
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_RESULT_LEDGER_DB", str(result_db))

    snapshot = date(2026, 9, 24)
    contracts = ContractMasterCatalog(str(contract_db))
    contracts.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "SBIN30SEP2026FUT", "1001", date(2026, 9, 30), "STOCK_FUTURE", "SBIN", 100, snapshot),
        ContractRecord("NFO", "SBIN29OCT2026FUT", "1002", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 100, snapshot),
    ])
    contracts.close()

    base = int(datetime(2026, 9, 24, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    data = HistoricalCatalog(str(data_db))
    data.ingest([
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1s", base, {"bid": 99, "ask": 100}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1s", base, {"bid": 103, "ask": 104}),
        HistoricalRecord("angelone", "NFO:1001:SBIN30SEP2026FUT", "1s", base + 1_000_000_000, {"bid": 102, "ask": 103}),
        HistoricalRecord("angelone", "NFO:1002:SBIN29OCT2026FUT", "1s", base + 1_000_000_000, {"bid": 98, "ask": 99}),
    ])
    data.close()

    result = calendar_spread_historical_strategy_run(CalendarSpreadHistoricalStrategyRunRequest(
        underlying="SBIN",
        exchange="NFO",
        start_date=snapshot,
        end_date=snapshot,
        near_contract_month="2026-09",
        far_contract_month="2026-10",
        source_timeframe="1s",
        replay_timeframe="1s",
        fees_per_unit=0.5,
    ))

    assert result["source"] == "historical-catalog"
    assert result["completed_trades"] == 1
    assert result["trade_count"] == 1
    assert result["net_profit"] == 500.0


def test_calendar_spread_contract_month_discovery_uses_point_in_time_snapshot(tmp_path, monkeypatch):
    from app.backtesting.calendar_spread_strategy_routes import (
        CalendarSpreadContractMonthsRequest,
        calendar_spread_contract_months,
    )
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord

    db = tmp_path / "contracts.sqlite"
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_CONTRACT_DB", str(db))
    catalog = ContractMasterCatalog(str(db))
    snapshot = date(2026, 9, 24)
    catalog.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "SBIN30SEP2026FUT", "1001", date(2026, 9, 30), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("NFO", "SBIN29OCT2026FUT", "1002", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("NFO", "SBIN26NOV2026FUT", "1003", date(2026, 11, 26), "STOCK_FUTURE", "SBIN", 150, snapshot),
    ])
    catalog.close()

    result = calendar_spread_contract_months(CalendarSpreadContractMonthsRequest(
        underlying="sbin", exchange="nfo", as_of=snapshot,
    ))
    assert [row["contract_month"] for row in result["contracts"]] == ["2026-09", "2026-10"]
    assert [row["lot_size"] for row in result["contracts"]] == [150, 150]


def test_calendar_spread_auto_contract_discovery_prioritizes_index_then_supports_commodity(tmp_path, monkeypatch):
    from app.backtesting.calendar_spread_strategy_routes import (
        CalendarSpreadContractMonthsRequest,
        calendar_spread_contract_months,
    )
    from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord

    db = tmp_path / "priority-contracts.sqlite"
    monkeypatch.setattr("app.backtesting.calendar_spread_strategy_routes.settings.BACKTEST_CONTRACT_DB", str(db))
    snapshot = date(2026, 9, 24)
    catalog = ContractMasterCatalog(str(db))
    catalog.upsert_snapshot(snapshot, [
        ContractRecord("NFO", "NIFTY30SEP2026FUT", "1101", date(2026, 9, 30), "INDEX_FUTURE", "NIFTY", 75, snapshot),
        ContractRecord("NFO", "NIFTY29OCT2026FUT", "1102", date(2026, 10, 29), "INDEX_FUTURE", "NIFTY", 75, snapshot),
        ContractRecord("NFO", "SBIN30SEP2026FUT", "1201", date(2026, 9, 30), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("NFO", "SBIN29OCT2026FUT", "1202", date(2026, 10, 29), "STOCK_FUTURE", "SBIN", 150, snapshot),
        ContractRecord("MCX", "GOLD30SEP2026FUT", "1301", date(2026, 9, 30), "COMMODITY_FUTURE", "GOLD", 100, snapshot),
        ContractRecord("MCX", "GOLD30OCT2026FUT", "1302", date(2026, 10, 30), "COMMODITY_FUTURE", "GOLD", 100, snapshot),
    ])
    catalog.close()

    nifty = calendar_spread_contract_months(CalendarSpreadContractMonthsRequest(
        underlying="NIFTY", as_of=snapshot,
    ))
    assert nifty["instrument_type"] == "INDEX_FUTURE"
    assert nifty["exchange"] == "NFO"
    assert [row["contract_month"] for row in nifty["contracts"]] == ["2026-09", "2026-10"]

    gold = calendar_spread_contract_months(CalendarSpreadContractMonthsRequest(
        underlying="GOLD", as_of=snapshot,
    ))
    assert gold["instrument_type"] == "COMMODITY_FUTURE"
    assert gold["exchange"] == "MCX"
    assert [row["contract_month"] for row in gold["contracts"]] == ["2026-09", "2026-10"]
