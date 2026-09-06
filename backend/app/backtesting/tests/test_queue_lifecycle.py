import pytest

from app.backtesting.execution import QueueEvidence
from app.backtesting.queue_lifecycle import QueueLifecycleState


def test_queue_lifecycle_preserves_dynamic_depletion():
    state = QueueLifecycleState(8)
    state = state.advance(QueueEvidence(price=100.0, executed_quantity=3))
    state = state.advance(QueueEvidence(price=100.0, cancelled_quantity_ahead=2))
    assert state.queue_ahead_quantity == 3
    assert state.generation == 0
    assert state.resting is True


def test_queue_lifecycle_cancel_then_reinsert_starts_new_generation():
    state = QueueLifecycleState(8).advance(
        QueueEvidence(price=100.0, executed_quantity=5)
    )
    cancelled = state.cancel()
    assert cancelled.resting is False
    reinserted = cancelled.reinsert(11)
    assert reinserted.queue_ahead_quantity == 11
    assert reinserted.generation == 1
    assert reinserted.resting is True


def test_cancelled_queue_ignores_late_depth_evidence():
    state = QueueLifecycleState(4).cancel()
    advanced = state.advance(QueueEvidence(price=100.0, executed_quantity=4))
    assert advanced == state


def test_queue_lifecycle_rejects_negative_values():
    with pytest.raises(ValueError):
        QueueLifecycleState(-1)
    with pytest.raises(ValueError):
        QueueLifecycleState(1).advance(
            QueueEvidence(price=100.0, executed_quantity=-1)
        )
