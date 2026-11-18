from datetime import date

import pytest

from app.backtesting.historical_contract_snapshot_import import (
    parse_historical_contract_records,
)


def _row(**overrides: object) -> dict:
    row = {
        "snapshot_date": "2025-09-26",
        "exchange": "NFO",
        "symbol": "ABC25OCTFUT",
        "token": "9001",
        "expiry": "2025-10-30",
        "instrument_type": "STOCK_FUTURE",
        "underlying": "ABC",
        "lot_size": 100,
        "tick_size": 0.05,
    }
    row.update(overrides)
    return row


def test_rejects_expiry_before_snapshot_date() -> None:
    with pytest.raises(ValueError, match="expiry must be on or after snapshot_date"):
        parse_historical_contract_records(
            [_row(expiry="2025-09-25")],
            source_date=date(2025, 9, 26),
        )


def test_rejects_non_positive_lot_size() -> None:
    with pytest.raises(ValueError, match="lot_size must be a positive integer"):
        parse_historical_contract_records(
            [_row(lot_size=0)],
            source_date=date(2025, 9, 26),
        )


def test_rejects_boolean_lot_size() -> None:
    with pytest.raises(ValueError, match="lot_size must be a positive integer"):
        parse_historical_contract_records(
            [_row(lot_size=True)],
            source_date=date(2025, 9, 26),
        )
