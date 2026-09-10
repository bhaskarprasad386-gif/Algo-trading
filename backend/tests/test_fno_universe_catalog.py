from datetime import date

from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.fno_universe import build_fno_universe, load_fno_universe


def _records():
    return (
        ContractRecord("NFO", "RELIANCE26SEP", "101", date(2026, 9, 24), "STOCK_FUTURE", "RELIANCE", 250),
        ContractRecord("NFO", "TCS26SEP", "102", date(2026, 9, 24), "STOCK_FUTURE", "TCS", 175),
        ContractRecord("NFO", "NIFTY26SEP", "201", date(2026, 9, 24), "INDEX_FUTURE", "NIFTY", 65),
        ContractRecord("NFO", "BANKNIFTY26SEP", "202", date(2026, 9, 24), "INDEX_FUTURE", "BANKNIFTY", 30),
    )


def test_build_fno_universe_discovers_all_provider_backed_underlyings():
    universe = build_fno_universe(_records(), snapshot_date=date(2026, 9, 1))
    assert universe.stock_underlyings == ("RELIANCE", "TCS")
    assert universe.index_underlyings == ("BANKNIFTY", "NIFTY")
    assert universe.underlyings == ("RELIANCE", "TCS", "BANKNIFTY", "NIFTY")
    assert universe.contract_count == 4


def test_load_fno_universe_uses_latest_snapshot_not_future_snapshot():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 8, 31), _records()[:2])
    catalog.upsert_snapshot(date(2026, 9, 5), _records())
    universe = load_fno_universe(catalog, as_of=date(2026, 9, 6))
    assert universe.snapshot_date == date(2026, 9, 5)
    assert universe.contract_count == 4


def test_load_fno_universe_rejects_when_no_snapshot_exists_by_date():
    catalog = ContractMasterCatalog()
    catalog.upsert_snapshot(date(2026, 9, 5), _records())
    try:
        load_fno_universe(catalog, as_of=date(2026, 9, 4))
    except LookupError:
        pass
    else:
        raise AssertionError("expected missing historical snapshot error")
