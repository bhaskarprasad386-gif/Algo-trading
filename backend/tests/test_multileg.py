import pytest

from app.execution.multileg import (
    LegFill,
    WatchdogAction,
    WatchdogPolicy,
    evaluate_watchdog,
    rollback_required,
    square_off_required,
)


def test_partial_fill_reports_remaining_quantity():
    fill = LegFill("leg-1", requested_quantity=10, filled_quantity=6)
    assert fill.remaining_quantity == 4
    assert not fill.is_complete
    assert rollback_required((fill,))


def test_complete_legs_do_not_require_rollback():
    fill = LegFill("leg-1", requested_quantity=10, filled_quantity=10)
    assert fill.is_complete
    assert not rollback_required((fill,))


def test_watchdog_waits_before_timeout_and_rolls_back_after_timeout():
    policy = WatchdogPolicy(timeout_ms=1000)
    assert evaluate_watchdog(999, False, policy).action == WatchdogAction.WAIT
    decision = evaluate_watchdog(1000, False, policy)
    assert decision.action == WatchdogAction.ROLLBACK
    assert decision.timed_out


def test_completed_order_does_not_trigger_watchdog():
    decision = evaluate_watchdog(5000, True, WatchdogPolicy())
    assert decision.action == WatchdogAction.WAIT
    assert not decision.timed_out


def test_kill_switch_requires_square_off_for_open_legs():
    assert square_off_required(True, 2)
    assert not square_off_required(True, 0)
    assert not square_off_required(False, 2)


def test_invalid_multileg_inputs_are_rejected():
    with pytest.raises(ValueError):
        LegFill("", 1, 0)
    with pytest.raises(ValueError):
        LegFill("leg-1", 1, 2)
    with pytest.raises(ValueError):
        WatchdogPolicy(timeout_ms=0)
    with pytest.raises(ValueError):
        evaluate_watchdog(-1, False, WatchdogPolicy())
    with pytest.raises(ValueError):
        square_off_required(True, -1)
