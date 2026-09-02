"""Deterministic execution safety controls."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyLimits:
    daily_loss_limit: float
    error_limit: int = 3

    def __post_init__(self) -> None:
        if self.daily_loss_limit < 0:
            raise ValueError("daily_loss_limit cannot be negative")
        if self.error_limit < 1:
            raise ValueError("error_limit must be at least 1")


@dataclass
class SafetyController:
    """Track kill-switch, daily loss and consecutive execution errors."""

    limits: SafetyLimits
    killed: bool = False
    daily_loss: float = 0.0
    errors: int = 0

    def record_pnl(self, pnl: float) -> None:
        self.daily_loss = max(self.daily_loss - pnl, 0.0)
        if self.daily_loss >= self.limits.daily_loss_limit:
            self.killed = True

    def record_error(self) -> None:
        self.errors += 1
        if self.errors >= self.limits.error_limit:
            self.killed = True

    def reset_errors(self) -> None:
        self.errors = 0

    def activate_kill_switch(self) -> None:
        self.killed = True

    def deactivate_kill_switch(self) -> None:
        self.killed = False

    def allow_execution(self) -> bool:
        return not self.killed
