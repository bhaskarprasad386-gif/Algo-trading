from datetime import date

import pytest

from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_contract_snapshot_import import (
    import_historical_contract_snapshot,
    parse_historical_contract_records,
)


def _row(snapshot_date: str = "2025-09-26") -> dict:
    return {
        "snapshot_date": snapshot_date,
        "exchange": "NFO",
        "symbol": "ABC25OCTFUT",
        "token": "9001",
        "expiry": "2025-10-30",
        "instrument_type": "STOCK_FUTURE",
        "underlying": "ABC",
        "lot_size": 100,
        "tick_size": 0.05,
    }


def test_import_requires_explicit_source_date_match() -> None:
    with pytest.raises(ValueError, match="does not match source_date"):
        parse_historical_contract_records(
            [_row()],
            source_date=date(2025, 9, 27),
        )


def test_import_rejects_duplicate_exchange_token() -> None:
    row = _row()
    duplicate = dict(row, symbol="ABC25NOVFUT", expiry="2025-11-27")
    with pytest.raises(ValueError, match="duplicate contract identity"):
        parse_historical_contract_records(
            [row, duplicate],
            source_date=date(2025, 9, 26),
        )


def test_import_persists_dated_snapshot_without_relabeling() -> None:
    catalog = ContractMasterCatalog()
    try:
        payload = b"genuine-source-payload"
        count = import_historical_contract_snapshot(
            catalog,
            [_row()],
            source_date=date(2025, 9, 26),
            payload=payload,
        )
        assert count == 1
        assert catalog.snapshot_dates() == (date(2025, 9, 26),)
        record = catalog.all_contracts(snapshot_date=date(2025, 9, 26))[0]
        assert record.snapshot_date == date(2025, 9, 26)
        assert record.token == "9001"
        assert record.expiry == date(2025, 10, 30)
    finally:
        catalog.close()
