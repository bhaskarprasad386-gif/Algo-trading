import pytest

from app.execution.safety import SafetyController, SafetyLimits


def test_manual_kill_switch_blocks_execution():
    controller = SafetyController(SafetyLimits(daily_loss_limit=1000))
    assert controller.allow_execution()
    controller.activate_kill_switch()
    assert not controller.allow_execution()
    controller.deactivate_kill_switch()
    assert controller.allow_execution()


def test_daily_loss_limit_activates_kill_switch():
    controller = SafetyController(SafetyLimits(daily_loss_limit=100))
    controller.record_pnl(-101)
    assert controller.daily_loss == pytest.approx(101)
    assert controller.killed
    assert not controller.allow_execution()


def test_daily_loss_trip_cannot_be_cleared_by_manual_deactivation():
    controller = SafetyController(SafetyLimits(daily_loss_limit=100))
    controller.record_pnl(-101)
    controller.deactivate_kill_switch()
    assert controller.killed
    assert not controller.allow_execution()


def test_profit_does_not_reduce_accumulated_daily_loss():
    controller = SafetyController(SafetyLimits(daily_loss_limit=1000))
    controller.record_pnl(-200)
    controller.record_pnl(50)
    assert controller.daily_loss == pytest.approx(150)


def test_error_circuit_breaker_activates_at_limit():
    controller = SafetyController(SafetyLimits(daily_loss_limit=1000, error_limit=2))
    controller.record_error()
    assert controller.allow_execution()
    controller.record_error()
    assert controller.killed
    assert not controller.allow_execution()


def test_error_trip_requires_explicit_counter_reset_and_deactivation():
    controller = SafetyController(SafetyLimits(daily_loss_limit=1000, error_limit=2))
    controller.record_error()
    controller.record_error()
    controller.deactivate_kill_switch()
    assert controller.killed
    controller.reset_errors()
    assert controller.killed
    controller.deactivate_kill_switch()
    assert controller.allow_execution()


def test_success_can_reset_error_counter_without_resetting_kill_switch():
    controller = SafetyController(SafetyLimits(daily_loss_limit=1000, error_limit=2))
    controller.record_error()
    controller.reset_errors()
    assert controller.errors == 0
    assert controller.allow_execution()


def test_non_finite_pnl_is_rejected():
    controller = SafetyController(SafetyLimits(daily_loss_limit=1000))
    for pnl in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            controller.record_pnl(pnl)


def test_nan_daily_loss_limit_is_rejected():
    with pytest.raises(ValueError):
        SafetyLimits(daily_loss_limit=float("nan"))


def test_invalid_safety_limits_are_rejected():
    with pytest.raises(ValueError):
        SafetyLimits(daily_loss_limit=-1)
    with pytest.raises(ValueError):
        SafetyLimits(daily_loss_limit=1000, error_limit=0)
