import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes(**overrides):
    values = {
        "symbol": "ABC",
        "contract_month": "2026-10",
        "ltp": 104.0,
        "lot_size": 100,
        "margin_required": 20_000.0,
        "volume": 10_000,
        "oi": 20_000,
        "bid": 103.8,
        "ask": 104.2,
    }
    values.update(overrides)
    return CashQuote("ABC", 100.0, 99.9, 100.1), FutureQuote(**values)


@pytest.mark.parametrize("field", ["volume", "oi"])
@pytest.mark.parametrize("value", [1.5, True, False])
def test_market_statistics_must_be_strict_non_negative_integers(field, value):
    cash, future = _quotes(**{field: value})

    with pytest.raises(ValueError, match=rf"{field} must be a non-negative integer"):
        calculate_cash_future(cash, future, CashFutureConfig())


@pytest.mark.parametrize("field", ["volume", "oi"])
def test_market_statistics_zero_and_positive_integers_are_accepted(field):
    cash, future = _quotes(**{field: 0})
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.executable is True
