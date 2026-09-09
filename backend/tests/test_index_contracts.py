from datetime import date

import pytest

from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.index_contracts import IndexContractMaster


def test_normalize_discovers_index_futures_without_hardcoded_underlyings():
    rows = [
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "ALPHA", "symbol": "ALPHA26SEP", "token": "1", "expiry": "24SEP2026", "lotsize": "50", "tick_size": "0.05"},
        {"exch_seg": "BFO", "instrumenttype": "FUTIDX", "name": "BETA", "symbol": "BETA26SEP", "token": "2", "expiry": "25SEP2026", "lotsize": "20", "tick_size": "0.10"},
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": "STOCK", "symbol": "STOCK26SEP", "token": "3", "expiry": "24SEP2026", "lotsize": "100"},
    ]
    records = IndexContractMaster.normalize(rows)
    assert [(r.exchange, r.underlying, r.instrument_type, r.token, r.tick_size) for r in records] == [
        ("NFO", "ALPHA", "INDEX_FUTURE", "1", 0.05),
        ("BFO", "BETA", "INDEX_FUTURE", "2", 0.10),
    ]


def test_resolve_near_next_far_from_historical_snapshot():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(
        date(2026, 9, 7),
        IndexContractMaster.normalize([
            {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "ALPHA", "symbol": "A1", "token": "101", "expiry": "24SEP2026", "lotsize": "50", "tick_size": "0.05"},
            {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "ALPHA", "symbol": "A2", "token": "102", "expiry": "29OCT2026", "lotsize": "50", "tick_size": "0.05"},
            {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "ALPHA", "symbol": "A3", "token": "103", "expiry": "26NOV2026", "lotsize": "50", "tick_size": "0.05"},
        ]),
    )
    assert IndexContractMaster.resolve(catalog, exchange="NFO", underlying="ALPHA", as_of=date(2026, 9, 7), rank="NEAR").token == "101"
    assert IndexContractMaster.resolve(catalog, exchange="NFO", underlying="ALPHA", as_of=date(2026, 9, 7), rank="NEXT").token == "102"
    assert IndexContractMaster.resolve(catalog, exchange="NFO", underlying="ALPHA", as_of=date(2026, 9, 7), rank="FAR").token == "103"
    catalog.close()


def test_resolve_fails_closed_when_rank_is_unavailable():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 9, 7), IndexContractMaster.normalize([
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "name": "ALPHA", "symbol": "A1", "token": "101", "expiry": "24SEP2026", "lotsize": "50"},
    ]))
    with pytest.raises(LookupError):
        IndexContractMaster.resolve(catalog, exchange="NFO", underlying="ALPHA", as_of=date(2026, 9, 7), rank="NEXT")
    catalog.close()
