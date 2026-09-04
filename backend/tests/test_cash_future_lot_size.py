import pytest

from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


def _quotes(lot_size):
    return (
        CashQuote(symbol="ABC", ltp=100.0, bid=99.9, ask=100.1),
        FutureQuote(
            symbol="ABC",
            contract_month="2026-10",
            ltp=104.0,
            lot_size=lot_size,
            margin_required=20_000.0,
            volume=10_000,
            oi=20_000,
            bid=103.8,
            ask=104.2,
        ),
    )


@pytest.mark.parametrize("lot_size", [0, -1])
def test_lot_size_must_be_positive(lot_size):
    cash, future = _quotes(lot_size)

    with pytest.raises(ValueError, match="lot_size must be greater than zero"):
        calculate_cash_future(cash, future, CashFutureConfig())


@pytest.mark.parametrize("lot_size", [100.5, True, False])
def test_lot_size_must_be_a_strict_integer(lot_size):
    cash, future = _quotes(lot_size)

    with pytest.raises(ValueError, match="lot_size must be a positive integer"):
        calculate_cash_future(cash, future, CashFutureConfig())
