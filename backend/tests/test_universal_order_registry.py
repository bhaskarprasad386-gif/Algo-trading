import pytest

from app.backtesting.execution import ExecutionResult, ExecutionSide, SimFill, SimOrder, TimeInForce
from app.backtesting.order_lifecycle import OrderStatus
from app.backtesting.queue_lifecycle import QueueEvidence
from app.backtesting.universal_order_registry import UniversalOrderRegistry


def make_order(order_id="o1", quantity=10, tif=TimeInForce.DAY, queue=5):
    return SimOrder(
        order_id,
        "NIFTY",
        ExecutionSide.BUY,
        quantity,
        submitted_at_ns=100,
        queue_ahead_quantity=queue,
        time_in_force=tif,
    )


def test_submit_duplicate_id_is_atomic():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(), 1000.0)
    before = registry.export_state()

    with pytest.raises(ValueError, match="already in use"):
        registry.submit(make_order(), 2000.0)

    assert registry.export_state() == before


def test_partial_day_fill_updates_lifecycle_queue_and_reservation():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(), 1000.0)
    result = ExecutionResult(
        (SimFill("o1", "NIFTY", ExecutionSide.BUY, 4, 100.0, 110),),
        6,
        False,
        "partial fill",
    )

    outcome = registry.apply_execution("o1", result, 110)

    assert outcome.status is OrderStatus.PARTIALLY_FILLED
    assert outcome.filled_quantity == 4
    assert outcome.remaining_quantity == 6
    assert outcome.released_reservation == pytest.approx(400.0)
    assert registry.reservation("o1") == pytest.approx(600.0)
    assert registry.effective_order("o1").quantity == 6


def test_full_fill_removes_open_order_and_releases_reservation():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(), 1000.0)
    result = ExecutionResult(
        (SimFill("o1", "NIFTY", ExecutionSide.BUY, 10, 100.0, 110),),
        0,
    )

    outcome = registry.apply_execution("o1", result, 110)

    assert outcome.status is OrderStatus.FILLED
    assert outcome.released_reservation == pytest.approx(1000.0)
    assert registry.open_orders() == ()
    assert registry.reservation("o1") == 0.0
    assert registry.queue_state("o1").resting is False


def test_partial_ioc_cancels_residual_and_releases_remaining_reservation():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(tif=TimeInForce.IOC), 1000.0)
    result = ExecutionResult(
        (SimFill("o1", "NIFTY", ExecutionSide.BUY, 4, 100.0, 110),),
        6,
        False,
        "IOC remainder cancelled",
    )

    outcome = registry.apply_execution("o1", result, 110)

    assert outcome.status is OrderStatus.CANCELLED
    assert outcome.remaining_quantity == 6
    assert outcome.released_reservation == pytest.approx(1000.0)
    assert registry.open_orders() == ()


def test_fok_rejection_changes_lifecycle_without_fills():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(tif=TimeInForce.FOK), 1000.0)
    result = ExecutionResult((), 10, True, "insufficient displayed depth")

    outcome = registry.apply_execution("o1", result, 110)

    assert outcome.status is OrderStatus.REJECTED
    assert outcome.filled_quantity == 0
    assert registry.open_orders() == ()
    assert registry.reservation("o1") == 0.0
    assert registry.lifecycle("o1").state.reject_reason == "insufficient displayed depth"


def test_queue_advance_flows_into_effective_execution_order():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(queue=8))

    registry.advance_queue("o1", QueueEvidence(100.0, executed_quantity=3))
    effective = registry.effective_order("o1")

    assert effective.queue_ahead_quantity == 5
    assert effective.quantity == 10


def test_replace_uses_remaining_quantity_and_new_queue_generation():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(queue=8), 1000.0)
    registry.apply_execution(
        "o1",
        ExecutionResult((SimFill("o1", "NIFTY", ExecutionSide.BUY, 4, 100.0, 110),), 6),
        110,
    )

    replacement = make_order("o2", quantity=6, queue=2)
    registry.replace("o1", replacement, 120)

    assert registry.lifecycle("o1").state.status is OrderStatus.REPLACED
    assert registry.get("o2") == replacement
    assert registry.queue_state("o2").queue_ahead_quantity == 2
    assert registry.queue_state("o2").generation == 1
    assert registry.reservation("o2") == pytest.approx(600.0)


def test_failed_execution_leaves_registry_state_unchanged():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(), 1000.0)
    before = registry.export_state()
    invalid = ExecutionResult(
        (SimFill("wrong", "NIFTY", ExecutionSide.BUY, 1, 100.0, 110),),
        9,
    )

    with pytest.raises(ValueError, match="order_id"):
        registry.apply_execution("o1", invalid, 110)

    assert registry.export_state() == before


def test_export_restore_preserves_open_order_lifecycle_queue_and_reservation():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(queue=8), 1000.0)
    registry.advance_queue("o1", QueueEvidence(100.0, executed_quantity=3))
    registry.apply_execution(
        "o1",
        ExecutionResult((SimFill("o1", "NIFTY", ExecutionSide.BUY, 4, 100.0, 110),), 6),
        110,
    )

    restored = UniversalOrderRegistry.restore_state(registry.export_state())

    assert restored.get("o1") == registry.get("o1")
    assert restored.lifecycle("o1").export_state() == registry.lifecycle("o1").export_state()
    assert restored.queue_state("o1") == registry.queue_state("o1")
    assert restored.reservation("o1") == pytest.approx(600.0)


def test_non_terminal_execution_rejection_keeps_resting_order():
    registry = UniversalOrderRegistry()
    registry.submit(make_order(), 1000.0)
    result = ExecutionResult((), 10, True, "queue ahead not depleted")

    outcome = registry.apply_execution("o1", result, 110)

    assert outcome.status is OrderStatus.ACCEPTED
    assert outcome.remaining_quantity == 10
    assert registry.open_orders() == (make_order(),)
    assert registry.reservation("o1") == pytest.approx(1000.0)
