from datetime import date, timedelta

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes(expiry):
    return (
        CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.1),
        FutureQuote(
            symbol="ABC",
            contract_month=expiry.strftime("%Y-%m"),
            ltp=104.0,
            lot_size=100,
            margin_required=20_000.0,
            volume=10_000,
            oi=20_000,
            bid=103.8,
            ask=104.2,
            expiry=expiry,
        ),
    )


def test_min_days_to_expiry_rejects_contract_too_close_to_expiry():
    cash, future = _quotes(date.today() + timedelta(days=4))

    result = calculate_cash_future(
        cash,
        future,
        CashFutureConfig(min_days_to_expiry=5),
    )

    assert result.executable is False
    assert "days_to_expiry_below_minimum" in result.rejection_reasons


def test_max_days_to_expiry_rejects_contract_too_far_from_expiry():
    cash, future = _quotes(date.today() + timedelta(days=31))

    result = calculate_cash_future(
        cash,
        future,
        CashFutureConfig(max_days_to_expiry=30),
    )

    assert result.executable is False
    assert "days_to_expiry_above_maximum" in result.rejection_reasons


def test_expiry_boundary_is_inclusive():
    cash, future = _quotes(date.today() + timedelta(days=5))

    result = calculate_cash_future(
        cash,
        future,
        CashFutureConfig(min_days_to_expiry=5, max_days_to_expiry=5),
    )

    assert result.executable is True
    assert result.rejection_reasons == ()
