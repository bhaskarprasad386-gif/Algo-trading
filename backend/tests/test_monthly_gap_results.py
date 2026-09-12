from datetime import date

import pytest

from app.backtesting.monthly_gap_results import (
    MonthlyGapRow,
    build_monthly_rows,
    monthly_graph,
    search_monthly_largest_gap,
)


def row(day: int, symbol: str, open_: float, high: float, lot: int, previous: float | None = 100.0, kind: str = "STOCK", contract: str | None = None):
    return MonthlyGapRow(date(2026, 9, day), symbol, open_, high, open_ - 2, open_ + 1, lot, previous, kind, contract)


def test_monthly_opening_gap_uses_historical_lot_size():
    rows = [row(2, "A", 110, 112, 500), row(3, "B", 108, 109, 1000)]
    result = search_monthly_largest_gap(rows, year=2026, month=9)
    assert result is not None
    assert result.symbol == "A"
    assert result.trading_date == date(2026, 9, 2)
    assert result.gap_value == 5000


def test_monthly_shorting_gap_is_high_minus_open_times_lot():
    rows = [row(2, "A", 100, 108, 500), row(3, "B", 200, 207, 1000)]
    result = search_monthly_largest_gap(rows, year=2026, month=9, mode="shorting")
    assert result is not None
    assert result.symbol == "B"
    assert result.gap == 7
    assert result.gap_value == 7000


def test_monthly_shorting_gap_can_be_scoped_to_selected_symbol():
    rows = [row(2, "A", 100, 108, 500), row(3, "A", 102, 111, 500), row(4, "B", 200, 230, 1000)]
    result = search_monthly_largest_gap(rows, year=2026, month=9, mode="shorting", symbol="a")
    assert result is not None
    assert result.symbol == "A"
    assert result.trading_date == date(2026, 9, 3)
    assert result.gap == 9
    assert result.gap_value == 4500


def test_monthly_graph_returns_all_trading_days_sorted():
    rows = [row(3, "A", 103, 106, 500), row(1, "A", 101, 104, 500), row(4, "B", 110, 111, 100)]
    points = monthly_graph(rows, year=2026, month=9, symbol="a")
    assert [p.trading_date for p in points] == [date(2026, 9, 1), date(2026, 9, 3)]
    assert points[0].open_price == 101
    assert points[1].high == 106


def test_builder_preserves_futures_identity_and_contract_month():
    rows = build_monthly_rows([
        {"trading_date": "2026-09-02", "symbol": "A", "open": 100, "high": 108, "low": 98, "close": 105, "lot_size": 500,
         "previous_close": 99, "instrument_type": "future", "contract_month": "2026-09"}
    ])
    result = search_monthly_largest_gap(rows, year=2026, month=9, instrument_type="FUTURE", contract_month="2026-09")
    assert result is not None
    assert result.instrument_type == "FUTURE"
    assert result.contract_month == "2026-09"


def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        search_monthly_largest_gap([], year=2026, month=9, mode="bad")
