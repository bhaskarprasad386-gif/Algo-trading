import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes():
    return (
        CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.1),
        FutureQuote(
            symbol="ABC",
            contract_month="2026-10",
            ltp=104.0,
            lot_size=100,
            margin_required=20_000.0,
            volume=10_000,
            oi=20_000,
            bid=103.8,
            ask=104.2,
        ),
    )


def test_cash_future_rejects_negative_charges():
    with pytest.raises(ValueError, match="charges"):
        CashFutureConfig(charges=-1.0)


def test_cash_future_rejects_negative_funding_cost():
    with pytest.raises(ValueError, match="funding_cost"):
        CashFutureConfig(funding_cost=-1.0)


def test_cash_future_rejects_negative_thresholds():
    with pytest.raises(ValueError, match="min_gap"):
        CashFutureConfig(min_gap=-0.01)


def test_cash_future_rejects_invalid_margin_range():
    with pytest.raises(ValueError, match="max_margin"):
        CashFutureConfig(min_margin=10_000.0, max_margin=9_999.0)


def test_cash_future_rejects_invalid_days_to_expiry_range():
    with pytest.raises(ValueError, match="max_days_to_expiry"):
        CashFutureConfig(min_days_to_expiry=10, max_days_to_expiry=5)


def test_cash_future_accepts_valid_cost_configuration():
    cash, future = _quotes()
    result = calculate_cash_future(
        cash,
        future,
        CashFutureConfig(charges=10.0, funding_cost=20.0, min_gap=0.0),
    )
    assert result.net_profit == 348.0
    assert result.executable is True
