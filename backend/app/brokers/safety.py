from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class TradingSafetyState:
    """Per-user live-trading safety state kept in process memory.

    Live order routing remains disabled in the broker adapters. This state only
    represents an explicit, reversible safety arming decision.
    """

    real_trading_enabled: bool = False
    kill_switch: bool = True
    enabled_at: datetime | None = None


class TradingSafetyStore:
    def __init__(self) -> None:
        self._states: dict[int, TradingSafetyState] = {}

    def get(self, user_id: int) -> TradingSafetyState:
        return self._states.setdefault(user_id, TradingSafetyState())

    def enable(self, user_id: int) -> TradingSafetyState:
        state = self.get(user_id)
        state.real_trading_enabled = True
        state.kill_switch = False
        state.enabled_at = datetime.now(timezone.utc)
        return state

    def disable(self, user_id: int) -> TradingSafetyState:
        state = self.get(user_id)
        state.real_trading_enabled = False
        state.kill_switch = True
        state.enabled_at = None
        return state


trading_safety = TradingSafetyStore()
