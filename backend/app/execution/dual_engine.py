"""Small paper/live execution engine foundation with fill-based SL/target adjustment."""

from dataclasses import dataclass
from enum import Enum
from typing import Callable


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


FillExecutor = Callable[[ExecutionMode, float], Fill]


class DualExecutionEngine:
    """Keep paper/live state separate while processing the same signal."""

    def __init__(self, fill_executor: FillExecutor, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()
        self._fill_executor = fill_executor
        self.paper = ExecutionState(ExecutionMode.PAPER)
        self.live = ExecutionState(ExecutionMode.LIVE)

    def enter(self, price: float, quantity: float) -> tuple[Fill, Fill]:
        """Submit the synchronized entry to both modes and adjust from actual fills."""
        paper_fill = self._fill_executor(ExecutionMode.PAPER, price)
        live_fill = self._fill_executor(ExecutionMode.LIVE, price)
        self.paper.apply_fill(paper_fill, self.config)
        self.live.apply_fill(live_fill, self.config)
        return paper_fill, live_fill
