from app.execution.strategy_legs import (
    StrategyLegInput,
    build_cash_future_strategy,
    build_strategy_legs,
)


def test_cash_future_builder_creates_explicit_two_legs():
    legs = build_cash_future_strategy(
        cash_entry_price=100.0,
        future_entry_price=105.0,
        quantity=10,
    )

    assert [(leg.kind, leg.side, leg.entry_price, leg.quantity) for leg in legs] == [
        ("SPOT", "BUY", 100.0, 10),
        ("FUTURE", "SELL", 105.0, 10),
    ]


def test_builder_preserves_option_strike_and_multiplier():
    legs = build_strategy_legs(
        (
            StrategyLegInput("CALL", "BUY", 12.5, 25, strike=25000, multiplier=50),
            StrategyLegInput("PUT", "SELL", 8.0, 25, strike=24500, multiplier=50),
        )
    )

    assert legs[0].strike == 25000
    assert legs[0].multiplier == 50
    assert legs[1].strike == 24500


def test_builder_rejects_missing_strategy():
    try:
        build_strategy_legs(())
    except ValueError as exc:
        assert "at least one leg" in str(exc)
    else:
        raise AssertionError("empty strategy should be rejected")


def test_cash_future_builder_rejects_non_positive_spread():
    try:
        build_cash_future_strategy(
            cash_entry_price=105.0,
            future_entry_price=105.0,
            quantity=10,
        )
    except ValueError as exc:
        assert "must exceed" in str(exc)
    else:
        raise AssertionError("non-positive cash/future spread should be rejected")
