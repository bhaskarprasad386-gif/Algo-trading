import pytest

from app.backtesting.execution import (
    DepthLevel,
    ExecutionSide,
    QueueEvidence,
    SimFill,
    SimOrder,
)


def test_sim_order_rejects_fractional_quantity():
    with pytest.raises(ValueError, match="positive integer"):
        SimOrder("o1", "NSE:ABC", ExecutionSide.BUY, 1.5)


def test_sim_order_rejects_boolean_quantity():
    with pytest.raises(ValueError, match="positive integer"):
        SimOrder("o1", "NSE:ABC", ExecutionSide.BUY, True)


def test_depth_and_fill_reject_fractional_quantities():
    with pytest.raises(ValueError, match="non-negative integer"):
        DepthLevel(100.0, 2.5)
    with pytest.raises(ValueError, match="positive integer"):
        SimFill("f1", "NSE:ABC", ExecutionSide.BUY, 1.5, 100.0, 1)


def test_queue_evidence_rejects_fractional_quantities():
    with pytest.raises(ValueError, match="non-negative integer"):
        QueueEvidence(100.0, executed_quantity=1.5)
    with pytest.raises(ValueError, match="non-negative integer"):
        QueueEvidence(100.0, cancelled_quantity_ahead=1.5)
