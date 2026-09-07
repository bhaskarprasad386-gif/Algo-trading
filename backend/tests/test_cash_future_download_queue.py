from datetime import date, datetime, timezone

from app.backtesting.cash_future_download_queue import build_rollover_download_queue
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord


def _catalog():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 1, 1), [
        ContractRecord("NFO", "SBINJAN", "101", date(2026, 1, 29), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    catalog.upsert_snapshot(date(2026, 1, 30), [
        ContractRecord("NFO", "SBINFEB", "102", date(2026, 2, 26), "STOCK_FUTURE", "SBIN", 750),
        ContractRecord("NFO", "SBINMAR", "103", date(2026, 3, 26), "STOCK_FUTURE", "SBIN", 750),
    ])
    return catalog


def test_queue_uses_exact_historical_tokens_per_segment():
    queue = build_rollover_download_queue(
        catalog=_catalog(), spot_instrument="NSE:3045:SBIN",
        exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        timeframe="1m", mode="CURRENT",
    )
    assert queue.spot.instrument == "NSE:3045:SBIN"
    assert [x.request.instrument for x in queue.futures] == ["NFO:101:SBINJAN", "NFO:102:SBINFEB"]
    assert queue.futures[0].request.end_ns < queue.futures[1].request.start_ns


def test_queue_propagates_custom_historical_provider_to_every_request():
    queue = build_rollover_download_queue(
        catalog=_catalog(), spot_instrument="NSE:3045:SBIN",
        exchange="NFO", underlying="SBIN",
        start=datetime(2026, 1, 29, tzinfo=timezone.utc),
        end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        timeframe="1m", mode="CURRENT", source="other-provider",
    )
    assert queue.spot.source == "other-provider"
    assert [item.request.source for item in queue.futures] == ["other-provider", "other-provider"]
