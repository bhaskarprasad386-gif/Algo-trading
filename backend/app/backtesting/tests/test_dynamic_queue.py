import pytest

from app.backtesting.execution import ExecutionSimulator, QueueEvidence


def test_queue_advances_only_from_explicit_execution_or_cancellation_evidence():
    simulator = ExecutionSimulator()
    evidence = QueueEvidence(price=100.0, executed_quantity=3, cancelled_quantity_ahead=2)
    assert simulator.advance_queue_ahead(10, evidence) == 5


def test_queue_cannot_go_below_zero():
    simulator = ExecutionSimulator()
    evidence = QueueEvidence(price=100.0, executed_quantity=9, cancelled_quantity_ahead=9)
    assert simulator.advance_queue_ahead(5, evidence) == 0


def test_book_disappearance_without_evidence_does_not_advance_queue():
    simulator = ExecutionSimulator()
    # A missing level alone is ambiguous: it may be cancellation, trade, or
    # feed loss. The conservative model therefore leaves queue position intact.
    no_evidence = QueueEvidence(price=100.0)
    assert simulator.advance_queue_ahead(5, no_evidence) == 5


def test_negative_queue_or_evidence_is_rejected():
    simulator = ExecutionSimulator()
    with pytest.raises(ValueError):
        simulator.advance_queue_ahead(-1, QueueEvidence(price=100.0))
    with pytest.raises(ValueError):
        QueueEvidence(price=100.0, executed_quantity=-1)
