from app.backtesting.arbitrage_backtester import (
    BoxSpreadBacktester,
    OptionQuote,
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


