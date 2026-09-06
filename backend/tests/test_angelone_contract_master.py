from app.backtesting.angelone_contract_master import AngelOneContractMasterSource


def test_normalize_stock_futures_only():
    rows = [
        {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "25SEP2026", "token": "101", "symbol": "SBIN25SEP26FUT", "name": "SBIN", "lotsize": "750"},
        {"exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "25SEP2026", "token": "102", "symbol": "NIFTY25SEP26FUT", "name": "NIFTY", "lotsize": "75"},
        {"exch_seg": "NFO", "instrumenttype": "OPTSTK", "expiry": "25SEP2026", "token": "103", "symbol": "SBIN25SEP26900CE", "name": "SBIN", "lotsize": "750"},
    ]

    records = AngelOneContractMasterSource.normalize_futures(rows)

    assert len(records) == 1
    assert records[0].token == "101"
    assert records[0].underlying == "SBIN"
    assert records[0].lot_size == 750


def test_invalid_expiry_is_skipped():
    rows = [{"exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "bad", "token": "101", "symbol": "SBINFUT", "name": "SBIN", "lotsize": "750"}]
    assert AngelOneContractMasterSource.normalize_futures(rows) == ()
