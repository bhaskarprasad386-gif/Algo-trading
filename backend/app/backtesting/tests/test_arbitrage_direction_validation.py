from app.backtesting.arbitrage_backtester import (
    BoxSpreadBacktester,
    FutureQuote,
    OptionQuote,
    SyntheticCashCarryBacktester,
)


def option():
    return OptionQuote(1, "ABC", 20261231, 100, 8, 9, 6, 7, lot_size=10)


def test_box_rejects_invalid_direction():
    low = option()
    high = OptionQuote(1, "ABC", 20261231, 110, 7, 8, 5, 6, lot_size=10)
    try:
        BoxSpreadBacktester.evaluate(low, high, direction="INVALID")
    except ValueError as exc:
        assert "direction" in str(exc)
    else:
        raise AssertionError("expected invalid direction rejection")


def test_synthetic_cash_carry_rejects_invalid_direction():
    opt = option()
    future = FutureQuote(1, "ABC", 20261231, 104, 105, lot_size=10)
    try:
        SyntheticCashCarryBacktester.evaluate(
            opt, future, time_to_expiry_years=0.5, direction="INVALID"
        )
    except ValueError as exc:
        assert "direction" in str(exc)
    else:
        raise AssertionError("expected invalid direction rejection")
