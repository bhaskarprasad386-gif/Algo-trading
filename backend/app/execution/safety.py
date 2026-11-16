"""Deterministic execution safety controls."""

from dataclasses import dataclass
from math import isfinite, isnan


@dataclass(frozen=True)
class SafetyLimits:
    daily_loss_limit: float
    error_limit: int = 3

    def __post_init__(self) -> None:
        if isnan(self.daily_loss_limit) or self.daily_loss_limit < 0:
            raise ValueError("daily_loss_limit must be non-negative and not NaN")
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
        if not isfinite(pnl):
            raise ValueError("pnl must be finite")
        self.daily_loss = max(self.daily_loss - pnl, 0.0)
        if self.daily_loss >= self.limits.daily_loss_limit:
            self.killed = True

    def record_error(self) -> None:
        self.errors += 1
        if self.errors >= self.limits.error_limit:
            self.killed = True

    def reset_errors(self) -> None:
        """Clear the error counter; does not itself release a tripped kill switch."""
        self.errors = 0

    def activate_kill_switch(self) -> None:
        self.killed = True

    def deactivate_kill_switch(self) -> None:
        """Release manual kill only when no automatic risk trip remains active.

        Error trips require an explicit reset_errors() followed by this method.
        Daily-loss trips remain latched for the session; this controller has no
        daily-loss reset operation.
        """
        if self.daily_loss >= self.limits.daily_loss_limit:
            return
        if self.errors >= self.limits.error_limit:
            return
        self.killed = False

    def allow_execution(self) -> bool:
        return not self.killed
