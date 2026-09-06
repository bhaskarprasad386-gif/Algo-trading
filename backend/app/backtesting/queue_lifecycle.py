"""Evidence-backed queue lifecycle state for depth-aware order simulation."""

from dataclasses import dataclass

from app.backtesting.execution import QueueEvidence


@dataclass(frozen=True)
class QueueLifecycleState:
    """Immutable queue state for one resting limit order."""

    queue_ahead_quantity: int
    generation: int = 0
    resting: bool = True

    def __post_init__(self) -> None:
        if self.queue_ahead_quantity < 0:
            raise ValueError("queue_ahead_quantity cannot be negative")
        if self.generation < 0:
            raise ValueError("generation cannot be negative")

    def advance(self, evidence: QueueEvidence) -> "QueueLifecycleState":
        """Consume explicit execution/cancellation evidence while resting."""
        if not self.resting:
            return self
        if evidence.executed_quantity < 0 or evidence.cancelled_quantity_ahead < 0:
            raise ValueError("queue evidence quantities cannot be negative")
        consumed = evidence.executed_quantity + evidence.cancelled_quantity_ahead
        return QueueLifecycleState(max(0, self.queue_ahead_quantity - consumed), self.generation, True)

    def cancel(self) -> "QueueLifecycleState":
        """Remove the order from the book."""
        return QueueLifecycleState(self.queue_ahead_quantity, self.generation, False)

    def reinsert(self, queue_ahead_quantity: int) -> "QueueLifecycleState":
        """Create a new queue position after cancel/replace/reinsert."""
        return QueueLifecycleState(queue_ahead_quantity, self.generation + 1, True)
