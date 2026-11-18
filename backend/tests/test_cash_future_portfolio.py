import pytest

from app.backtesting.cash_future_portfolio import CashFuturePortfolioLedger


def test_portfolio_reserves_multiple_positions_and_reports_available_capital():
    ledger = CashFuturePortfolioLedger(100_000.0)
    assert ledger.reserve(("ABC", "SEP"), 30_000.0)
    assert ledger.reserve(("XYZ", "SEP"), 40_000.0)
    assert ledger.open_position_count == 2
    assert ledger.reserved_margin == 70_000.0
    assert ledger.available_capital == 30_000.0


def test_portfolio_blocks_only_the_over_allocated_entry():
    ledger = CashFuturePortfolioLedger(50_000.0)
    assert ledger.reserve(("ABC", "SEP"), 30_000.0)
    assert not ledger.reserve(("XYZ", "SEP"), 25_000.0)
    assert ledger.blocked_entries == 1
    assert ledger.open_position_count == 1
    assert ledger.reserved_margin == 30_000.0
    assert ledger.available_capital == 20_000.0


def test_portfolio_releases_exact_position_reservation_and_applies_realized_pnl():
    ledger = CashFuturePortfolioLedger(100_000.0)
    ledger.reserve(("ABC", "SEP"), 30_000.0)
    ledger.reserve(("XYZ", "SEP"), 20_000.0)

    assert ledger.release(("ABC", "SEP")) == 30_000.0
    ledger.apply_realized_pnl(4_500.0)

    assert ledger.open_position_count == 1
    assert ledger.reserved_margin == 20_000.0
    assert ledger.available_capital == 84_500.0
    assert ledger.reservation(("XYZ", "SEP")).margin_required == 20_000.0


def test_portfolio_rejects_duplicate_or_unknown_position_reservations():
    ledger = CashFuturePortfolioLedger(100_000.0)
    ledger.reserve(("ABC", "SEP"), 10_000.0)
    with pytest.raises(ValueError, match="already has"):
        ledger.reserve(("ABC", "SEP"), 5_000.0)
    with pytest.raises(ValueError, match="no capital reservation"):
        ledger.release(("MISSING", "SEP"))
