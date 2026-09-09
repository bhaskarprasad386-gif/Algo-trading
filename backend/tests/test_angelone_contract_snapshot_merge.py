from datetime import date

from app.backtesting.angelone_contract_master import AngelOneContractMasterSource
from app.backtesting.contract_master import ContractMasterCatalog


def test_full_sync_retains_stock_and_index_segments(monkeypatch):
    rows = [
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "25SEP2026", "token": "101", "symbol": "SBIN25SEP26FUT", "name": "SBIN", "lotsize": "750"},
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "25SEP2026", "token": "102", "symbol": "NIFTY25SEP26FUT", "name": "NIFTY", "lotsize": "75"},
    ]
    source = AngelOneContractMasterSource()
    monkeypatch.setattr(source, "fetch", lambda: tuple(rows))
    catalog = ContractMasterCatalog()

    assert source.sync(catalog, snapshot_date=date(2026, 9, 7)) == 2
    stock = catalog.contracts(exchange="NFO", underlying="SBIN", as_of=date(2026, 9, 7), instrument_type="STOCK_FUTURE")
    index = catalog.contracts(exchange="NFO", underlying="NIFTY", as_of=date(2026, 9, 7), instrument_type="INDEX_FUTURE")
    assert stock[0].token == "101"
    assert index[0].token == "102"
    catalog.close()
