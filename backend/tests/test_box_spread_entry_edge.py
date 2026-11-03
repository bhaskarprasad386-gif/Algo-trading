import pytest

from app.scanner.box_spread_entry_edge import (
    effective_entry_edge,
    minimum_entry_edge,
    validate_entry_edge,
)


@pytest.mark.parametrize(
    ("alert", "minimum"),
    [(5, 4), (8, 7), (10, 9), (11, 9), (15, 13), (20, 18), (21, 18), (25, 22)],
)
def test_alert_edge_bands(alert, minimum):
    assert minimum_entry_edge(alert) == minimum


@pytest.mark.parametrize("alert", [0, 4.99, float("nan"), float("inf"), None])
def test_alert_edge_below_five_or_invalid_fails_closed(alert):
    with pytest.raises(ValueError):
        minimum_entry_edge(alert)


def test_limit_prices_cannot_reduce_long_edge_below_alert_floor():
    reference = {"LOW_CE": 100, "HIGH_CE": 80, "HIGH_PE": 100, "LOW_PE": 80}
    # Width 100; reference debit 40; reference edge 60.
    limits = {"LOW_CE": 105, "HIGH_CE": 75, "HIGH_PE": 105, "LOW_PE": 75}
    # Worse buy limits and worse sell limits reduce edge to 40.
    assert effective_entry_edge(
        direction="LONG", low_strike=22000, high_strike=22100,
        reference_prices=reference, limit_prices=limits,
    ) == 40
    with pytest.raises(ValueError, match="all four paper legs rejected"):
        validate_entry_edge(
            alert_edge=44, direction="LONG", low_strike=22000,
            high_strike=22100, reference_prices=reference, limit_prices=limits,
        )


def test_limit_prices_equal_or_better_than_reference_do_not_overstate_edge():
    reference = {"LOW_CE": 100, "HIGH_CE": 80, "HIGH_PE": 100, "LOW_PE": 80}
    favorable_limits = {"LOW_CE": 95, "HIGH_CE": 85, "HIGH_PE": 95, "LOW_PE": 85}
    edge = effective_entry_edge(
        direction="LONG", low_strike=22000, high_strike=22100,
        reference_prices=reference, limit_prices=favorable_limits,
    )
    assert edge == 60
    result = validate_entry_edge(
        alert_edge=63, direction="LONG", low_strike=22000,
        high_strike=22100, reference_prices=reference, limit_prices=favorable_limits,
    )
    assert result["minimum_entry_edge"] == 60
    assert result["effective_entry_edge"] == 60


def test_short_edge_uses_conservative_buy_and_sell_prices():
    reference = {"LOW_CE": 120, "HIGH_CE": 80, "HIGH_PE": 120, "LOW_PE": 80}
    limits = {"LOW_CE": 125, "HIGH_CE": 75, "HIGH_PE": 125, "LOW_PE": 75}
    # For SHORT, the buys are HIGH_CE/LOW_PE and sells are LOW_CE/HIGH_PE.
    # Conservative prices reduce the net credit from 80 to 60.
    assert effective_entry_edge(
        direction="SHORT", low_strike=22000, high_strike=22100,
        reference_prices=reference, limit_prices=limits,
    ) == -20
