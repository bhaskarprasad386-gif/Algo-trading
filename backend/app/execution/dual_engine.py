"""Small paper/live execution engine foundation with fill-based SL/target adjustment."""

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .confirmation import ConfirmationGateway
from .idempotency import ExecutionRequest, IdempotencyGuard
from .safety import SafetyController, SafetyLimits


class ExecutionMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class ExecutionConfig:
    stop_loss_pct: float = 0.02
    target_pct: float = 0.04

    def __post_init__(self) -> None:
        if self.stop_loss_pct < 0 or self.target_pct < 0:
            raise ValueError("stop_loss_pct and target_pct cannot be negative")


@dataclass(frozen=True)
class Fill:
    price: float
    quantity: float

    def __post_init__(self) -> None:
        if self.price <= 0 or self.quantity <= 0:
            raise ValueError("fill price and quantity must be positive")


@dataclass
class ExecutionState:
    mode: ExecutionMode
    quantity: float = 0.0
    entry_price: float | None = None
    stop_loss: float | None = None
    target: float | None = None

    def apply_fill(self, fill: Fill, config: ExecutionConfig) -> None:
        self.quantity = fill.quantity
        self.entry_price = fill.price
        self.stop_loss = fill.price * (1.0 - config.stop_loss_pct)
        self.target = fill.price * (1.0 + config.target_pct)


FillExecutor = Callable[[ExecutionMode, float, float], Fill]


class DualExecutionEngine:
    """Keep paper/live state separate and require explicit gated live entry."""

    def __init__(
        self,
        fill_executor: FillExecutor,
        config: ExecutionConfig | None = None,
        confirmation: ConfirmationGateway | None = None,
        idempotency: IdempotencyGuard | None = None,
        safety: SafetyController | None = None,
    ) -> None:
        self.config = config or ExecutionConfig()
        self._fill_executor = fill_executor
        self.confirmation = confirmation or ConfirmationGateway()
        self.idempotency = idempotency or IdempotencyGuard()
        # Preserve the legacy constructor while keeping explicit safety injection
        # available for real/live configuration and limits.
        self.safety = safety or SafetyController(SafetyLimits(daily_loss_limit=float("inf")))
        self.paper = ExecutionState(ExecutionMode.PAPER)
        self.live = ExecutionState(ExecutionMode.LIVE)

    def enter(self, price: float, quantity: float) -> Fill:
        """Execute a paper-only entry; live execution must use enter_live()."""
        paper_fill = self._fill_executor(ExecutionMode.PAPER, price, quantity)
        self.paper.apply_fill(paper_fill, self.config)
        return paper_fill

    def create_live_confirmation(self, request_id: str):
        """Create the short-lived confirmation required for a live entry."""
        return self.confirmation.create(request_id)

    def enter_live(self, price: float, quantity: float, request_id: str) -> Fill:
        """Execute one live entry only after safety, confirmation and idempotency checks."""
        if not self.safety.allow_execution():
            raise RuntimeError("live execution blocked by safety controller")
        if not self.confirmation.confirm(request_id):
            raise RuntimeError("live execution requires a valid confirmation")
        if not self.idempotency.accept(ExecutionRequest(request_id)):
            raise RuntimeError("duplicate live execution request")

        live_fill = self._fill_executor(ExecutionMode.LIVE, price, quantity)
        self.live.apply_fill(live_fill, self.config)
        return live_fill
