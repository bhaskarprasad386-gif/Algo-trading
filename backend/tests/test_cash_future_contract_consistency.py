from datetime import date

import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes(contract_month, expiry):
    return (
        CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.1),
        FutureQuote(
            symbol="ABC",
            contract_month=contract_month,
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


def test_contract_month_must_use_year_month_format_when_not_current():
    cash, future = _quotes("202610", date(2026, 10, 29))
    with pytest.raises(ValueError, match="contract_month must use YYYY-MM format"):
        calculate_cash_future(cash, future, CashFutureConfig())


def test_contract_month_must_match_expiry_month():
    cash, future = _quotes("2026-10", date(2026, 11, 26))
    with pytest.raises(ValueError, match="contract_month must match expiry month"):
        calculate_cash_future(cash, future, CashFutureConfig())


def test_valid_contract_month_and_expiry_are_accepted():
    cash, future = _quotes("2026-10", date(2026, 10, 29))
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.contract_month == "2026-10"


def test_current_contract_month_remains_supported():
    cash, future = _quotes("CURRENT", date(2026, 10, 29))
    result = calculate_cash_future(cash, future, CashFutureConfig())
    assert result.contract_month == "CURRENT"
