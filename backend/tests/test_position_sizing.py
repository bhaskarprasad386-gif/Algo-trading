import pytest

from app.execution.position_sizing import PositionSizingConfig, calculate_dynamic_quantity


CONFIG = PositionSizingConfig(risk_amount=10_000, reference_vix=15, atr_multiplier=1)


def test_atr_limits_quantity_when_it_is_larger_than_stop_distance():
    quantity = calculate_dynamic_quantity(100, 99, atr=5, india_vix=15, lot_size=10, config=CONFIG)
    assert quantity == 2_000


def test_higher_vix_reduces_quantity():
    normal = calculate_dynamic_quantity(100, 99, atr=2, india_vix=15, lot_size=10, config=CONFIG)
    high_vix = calculate_dynamic_quantity(100, 99, atr=2, india_vix=30, lot_size=10, config=CONFIG)
    assert high_vix < normal


def test_lower_vix_is_bounded_by_maximum_factor():
    quantity = calculate_dynamic_quantity(100, 99, atr=2, india_vix=1, lot_size=10, config=CONFIG)
    assert quantity == 7_500


def test_quantity_is_rounded_down_to_lot_size():
    quantity = calculate_dynamic_quantity(100, 98, atr=2, india_vix=15, lot_size=75, config=CONFIG)
    assert quantity == 4_950


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        calculate_dynamic_quantity(0, 99, 2, 15, config=CONFIG)
    with pytest.raises(ValueError):
        calculate_dynamic_quantity(100, 99, 0, 15, config=CONFIG)
    with pytest.raises(ValueError):
        calculate_dynamic_quantity(100, 99, 2, 15, lot_size=0, config=CONFIG)
    with pytest.raises(ValueError):
        PositionSizingConfig(risk_amount=0)
