import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _future(contract_month):
    return FutureQuote(
        symbol="ABC",
        contract_month=contract_month,
        ltp=104.0,
        lot_size=100,
        margin_required=20_000.0,
        volume=10_000,
        oi=20_000,
        bid=103.8,
        ask=104.2,
    )


def test_contract_month_must_be_non_empty_string():
    for value in ("", "   ", None, 202610, True):
        with pytest.raises(ValueError, match="contract_month must be a non-empty string"):
            calculate_cash_future(CashQuote("ABC", 100.0, 99.9, 100.1), _future(value), CashFutureConfig())


def test_contract_month_is_trimmed_in_result():
    result = calculate_cash_future(
        CashQuote("ABC", 100.0, 99.9, 100.1),
        _future(" 2026-10 "),
        CashFutureConfig(),
    )
    assert result.contract_month == "2026-10"
