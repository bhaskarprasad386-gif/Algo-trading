"""Safety policy for market-data provider failover.

This module is intentionally transport-agnostic: it decides which provider should
be authoritative, but it does not open sockets or route ticks/orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Literal

Provider = Literal["angel_one", "shoonya", "none"]


@dataclass(frozen=True)
class FailoverSnapshot:
    active_provider: Provider
    mode: str
    reason: str
    angel_healthy: bool
    shoonya_healthy: bool
    failed_angel_sockets: int
    switched_at: float | None


class MarketDataFailoverPolicy:
    """Hysteretic Angel One -> Shoonya -> Angel One health-based policy.

    Angel One is not abandoned for a single socket failure. Failover is requested
    only when >=2 Angel sockets are down or its quote feed is stale. Shoonya
    must be connected and have a fresh tick before it can be selected.
    """

    ANGEL_ONE: Provider = "angel_one"
    SHOONYA: Provider = "shoonya"
    NONE: Provider = "none"

    def __init__(
        self,
        *,
        angel_tick_fresh_seconds: float = 2.5,
        shoonya_tick_fresh_seconds: float = 2.5,
        angel_failover_stale_seconds: float = 4.0,
        angel_failback_stability_seconds: float = 10.0,
        minimum_shoonya_dwell_seconds: float = 20.0,
    ) -> None:
        values = (
            angel_tick_fresh_seconds,
            shoonya_tick_fresh_seconds,
            angel_failover_stale_seconds,
            angel_failback_stability_seconds,
            minimum_shoonya_dwell_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("failover timing values must be positive")
        self.angel_tick_fresh_seconds = angel_tick_fresh_seconds
        self.shoonya_tick_fresh_seconds = shoonya_tick_fresh_seconds
        self.angel_failover_stale_seconds = angel_failover_stale_seconds
        self.angel_failback_stability_seconds = angel_failback_stability_seconds
        self.minimum_shoonya_dwell_seconds = minimum_shoonya_dwell_seconds
        self._lock = RLock()
        self._active: Provider = self.ANGEL_ONE
        self._mode = "PRIMARY"
        self._reason = "startup_primary"
        self._switched_at: float | None = None
        self._angel_healthy_since: float | None = None

    def update(
        self,
        *,
        failed_angel_sockets: int,
        angel_tick_age_seconds: float | None,
        shoonya_connected: bool,
        shoonya_tick_age_seconds: float | None,
        now: float | None = None,
    ) -> FailoverSnapshot:
        """Return a provider selection decision using locally measured tick ages."""
        now = monotonic() if now is None else now
        if failed_angel_sockets < 0:
            raise ValueError("failed_angel_sockets cannot be negative")
        angel_fresh = (
            angel_tick_age_seconds is not None
            and 0 <= angel_tick_age_seconds <= self.angel_tick_fresh_seconds
        )
        shoonya_fresh = (
            shoonya_connected
            and shoonya_tick_age_seconds is not None
            and 0 <= shoonya_tick_age_seconds <= self.shoonya_tick_fresh_seconds
        )
        angel_healthy = failed_angel_sockets < 2 and angel_fresh
        failover_needed = (
            failed_angel_sockets >= 2
            or angel_tick_age_seconds is None
            or angel_tick_age_seconds >= self.angel_failover_stale_seconds
            or angel_tick_age_seconds < 0
        )

        with self._lock:
            if angel_healthy:
                if self._angel_healthy_since is None:
                    self._angel_healthy_since = now
            else:
                self._angel_healthy_since = None

            if self._active == self.ANGEL_ONE:
                if failover_needed:
                    if shoonya_fresh:
                        self._active = self.SHOONYA
                        self._mode = "BACKUP"
                        self._reason = (
                            "two_or_more_angel_sockets_failed"
                            if failed_angel_sockets >= 2
                            else "angel_feed_stale"
                        )
                        self._switched_at = now
                    else:
                        self._active = self.NONE
                        self._mode = "NO_VALID_FEED"
                        self._reason = "angel_unhealthy_shoonya_not_ready"
                else:
                    self._active = self.ANGEL_ONE
                    self._mode = "PRIMARY"
                    self._reason = "angel_healthy"
            elif self._active == self.SHOONYA:
                dwell_elapsed = (
                    self._switched_at is not None
                    and now - self._switched_at >= self.minimum_shoonya_dwell_seconds
                )
                stable_elapsed = (
                    self._angel_healthy_since is not None
                    and now - self._angel_healthy_since >= self.angel_failback_stability_seconds
                )
                if dwell_elapsed and stable_elapsed:
                    self._active = self.ANGEL_ONE
                    self._mode = "PRIMARY"
                    self._reason = "angel_stable_failback"
                    self._switched_at = now
                elif not shoonya_fresh:
                    self._active = self.NONE
                    self._mode = "NO_VALID_FEED"
                    self._reason = "shoonya_feed_stale"
                else:
                    self._mode = "BACKUP"
                    self._reason = "shoonya_active_waiting_for_angel_stability"
            else:
                if failover_needed and shoonya_fresh:
                    self._active = self.SHOONYA
                    self._mode = "BACKUP"
                    self._reason = "shoonya_validated_recovery"
                    self._switched_at = now
                elif angel_healthy:
                    self._active = self.ANGEL_ONE
                    self._mode = "PRIMARY"
                    self._reason = "angel_recovered"
                    self._switched_at = now
                else:
                    self._reason = "no_provider_feed_is_fresh"

            return FailoverSnapshot(
                active_provider=self._active,
                mode=self._mode,
                reason=self._reason,
                angel_healthy=angel_healthy,
                shoonya_healthy=shoonya_fresh,
                failed_angel_sockets=failed_angel_sockets,
                switched_at=self._switched_at,
            )

    def snapshot(self) -> FailoverSnapshot:
        with self._lock:
            return FailoverSnapshot(
                active_provider=self._active,
                mode=self._mode,
                reason=self._reason,
                angel_healthy=False,
                shoonya_healthy=False,
                failed_angel_sockets=0,
                switched_at=self._switched_at,
            )
