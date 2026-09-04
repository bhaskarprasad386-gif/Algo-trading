import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes():
    cash = CashQuote("ABC", 100.0, 99.9, 100.1)
    future = FutureQuote(
        symbol="ABC",
        contract_month="2026-10",
        ltp=104.0,
        lot_size=100,
        margin_required=20_000.0,
        volume=10_000,
        oi=20_000,
        bid=103.8,
        ask=104.2,
    )
    return cash, future


@pytest.mark.parametrize("field", ["min_gap", "min_gap_pct", "min_net_profit", "min_roi_pct", "min_margin", "charges", "funding_cost", "gap_match_tolerance"])
@pytest.mark.parametrize("value", ["5", True, None])
def test_numeric_thresholds_must_be_real_numbers(field, value):
    with pytest.raises(ValueError, match=rf"{field} must be a finite number"):
        CashFutureConfig(**{field: value})


def test_valid_numeric_thresholds_are_accepted():
    cash, future = _quotes()
    result = calculate_cash_future(cash, future, CashFutureConfig(min_gap=1, min_gap_pct=1.0))
    assert result.executable is True
