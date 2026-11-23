"""Safety policy for market-data provider failover.

This module is intentionally transport-agnostic: it decides which provider should
be authoritative, but it does not open sockets or route ticks/orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
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

    Socket-count degradation alone never switches providers. Angel One remains
    primary while its remaining socket(s) deliver fresh ticks. Failover is
    allowed only after feed freshness fails past the configured grace interval,
    and Shoonya must be connected with a fresh tick before selection.
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
        if any(not isfinite(value) or value <= 0 for value in values):
            raise ValueError("failover timing values must be finite and positive")
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
        self._last_update_at: float | None = None

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
        if not isfinite(now):
            raise ValueError("update timestamp must be finite")
        if (
            isinstance(failed_angel_sockets, bool)
            or not isinstance(failed_angel_sockets, int)
            or not 0 <= failed_angel_sockets <= 3
        ):
            raise ValueError("failed_angel_sockets must be an integer between 0 and 3")
        if not isinstance(shoonya_connected, bool):
            raise ValueError("shoonya_connected must be a boolean")
        for name, age in (
            ("angel_tick_age_seconds", angel_tick_age_seconds),
            ("shoonya_tick_age_seconds", shoonya_tick_age_seconds),
        ):
            if age is not None and (not isfinite(age) or age < 0):
                raise ValueError(f"{name} must be finite and non-negative when provided")
        angel_fresh = (
            angel_tick_age_seconds is not None
            and 0 <= angel_tick_age_seconds <= self.angel_tick_fresh_seconds
        )
        shoonya_fresh = (
            shoonya_connected
            and shoonya_tick_age_seconds is not None
            and 0 <= shoonya_tick_age_seconds <= self.shoonya_tick_fresh_seconds
        )
        angel_healthy = angel_fresh
        angel_stable = failed_angel_sockets < 2 and angel_fresh
        failover_needed = (
            angel_tick_age_seconds is None
            or angel_tick_age_seconds >= self.angel_failover_stale_seconds
            or (failed_angel_sockets >= 2 and not angel_fresh)
        )

        with self._lock:
            if self._last_update_at is not None and now < self._last_update_at:
                raise ValueError("out-of-order health update timestamp")
            self._last_update_at = now
            if angel_stable:
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
                            "two_or_more_angel_sockets_failed_and_feed_unhealthy"
                            if failed_angel_sockets >= 2 and not angel_fresh
                            else "angel_feed_stale"
                        )
                        self._switched_at = now
                    else:
                        self._active = self.NONE
                        self._mode = "NO_VALID_FEED"
                        self._reason = "angel_unhealthy_shoonya_not_ready"
                elif angel_fresh:
                    self._active = self.ANGEL_ONE
                    self._mode = "PRIMARY"
                    self._reason = "angel_healthy"
                else:
                    self._active = self.NONE
                    self._mode = "DEGRADED_WAIT"
                    self._reason = "angel_tick_stale_within_failover_grace"
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
                elif angel_fresh:
                    self._active = self.ANGEL_ONE
                    self._mode = "PRIMARY"
                    self._reason = "angel_recovered"
                    self._switched_at = now
                else:
                    self._mode = "NO_VALID_FEED"
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
