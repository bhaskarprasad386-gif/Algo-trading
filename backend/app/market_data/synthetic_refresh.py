"""Live synthetic subscription refresh coordination."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class SyntheticRefreshDecision:
    refresh: bool
    atm_strike: float


class SyntheticAtmRefreshGate:
    """Request a stream refresh only when the source-backed ATM changes."""

    def __init__(self, initial_atm: float) -> None:
        value = float(initial_atm)
        if value <= 0:
            raise ValueError("initial ATM must be positive")
        self._atm = value
        self._lock = Lock()

    @property
    def atm(self) -> float:
        with self._lock:
            return self._atm

    def observe(self, atm: float) -> SyntheticRefreshDecision:
        value = float(atm)
        if value <= 0:
            raise ValueError("ATM must be positive")
        with self._lock:
            changed = value != self._atm
            self._atm = value
        return SyntheticRefreshDecision(refresh=changed, atm_strike=value)


__all__ = ["SyntheticAtmRefreshGate", "SyntheticRefreshDecision"]
