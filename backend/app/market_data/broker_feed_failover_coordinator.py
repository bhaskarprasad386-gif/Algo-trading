"""Provider-selection bridge for canonical Angel One/Shoonya market-data records.

This coordinator is transport-neutral: adapters call observe() with canonical
records and health inputs; only records from the policy-selected provider are
forwarded to the consumer. It does not start sockets or subscribe instruments.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Literal

from .broker_feed_failover import MarketDataFailoverPolicy, FailoverSnapshot

Provider = Literal["angel_one", "shoonya"]


class MarketDataFailoverCoordinator:
    """Serialize provider decisions and forward only selected-provider ticks.

    The caller must wire both provider adapters and supply measured health ages.
    Timestamp monotonicity is enforced per instrument/provider so late ticks
    cannot regress a consumer's state.
    """

    def __init__(
        self,
        *,
        policy: MarketDataFailoverPolicy | None = None,
        on_record: Callable[[Provider, dict[str, Any]], None],
    ) -> None:
        if not callable(on_record):
            raise TypeError("on_record must be callable")
        self.policy = policy or MarketDataFailoverPolicy()
        self._on_record = on_record
        self._lock = RLock()
        self._latest: dict[tuple[Provider, str], dict[str, Any]] = {}
        self._last_timestamp: dict[tuple[Provider, str], int] = {}
        self._decision: FailoverSnapshot | None = None
        self._forwarded = 0
        self._suppressed = 0
        self._invalid_records = 0
        self._callback_errors = 0

    @staticmethod
    def _instrument_key(record: dict[str, Any]) -> str:
        key = record.get("instrument_key") or record.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("canonical record must include a non-empty instrument_key")
        return key.strip()

    @staticmethod
    def _timestamp_ns(record: dict[str, Any]) -> int:
        stamp = record.get("timestamp_ns")
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp <= 0:
            raise ValueError("canonical record timestamp_ns must be a positive integer")
        return stamp

    def observe(self, provider: Provider, record: dict[str, Any]) -> bool:
        """Cache a valid tick and forward it only if its provider is active."""
        if provider not in ("angel_one", "shoonya"):
            raise ValueError("provider must be angel_one or shoonya")
        if not isinstance(record, dict):
            raise TypeError("record must be a dict")
        try:
            key = self._instrument_key(record)
            stamp = self._timestamp_ns(record)
        except (TypeError, ValueError):
            with self._lock:
                self._invalid_records += 1
            return False
        identity = (provider, key)
        payload = dict(record)
        with self._lock:
            previous = self._last_timestamp.get(identity)
            if previous is not None and stamp <= previous:
                self._suppressed += 1
                return False
            self._last_timestamp[identity] = stamp
            self._latest[identity] = payload
            active = self._decision.active_provider if self._decision else "angel_one"
            if provider != active:
                self._suppressed += 1
                return False
            self._forwarded += 1
            # Serialize selection and forwarding so a provider switch cannot
            # overtake a tick already accepted by the active provider.
            self._on_record(provider, payload)
            return True

    def evaluate(
        self,
        *,
        failed_angel_sockets: int,
        angel_tick_age_seconds: float | None,
        shoonya_connected: bool,
        shoonya_tick_age_seconds: float | None,
        now: float | None = None,
    ) -> FailoverSnapshot:
        """Update policy using externally measured provider health."""
        # Serialize policy evaluation with observe() forwarding; otherwise a
        # concurrent evaluate could publish a provider switch mid-callback.
        with self._lock:
            decision = self.policy.update(
                failed_angel_sockets=failed_angel_sockets,
                angel_tick_age_seconds=angel_tick_age_seconds,
                shoonya_connected=shoonya_connected,
                shoonya_tick_age_seconds=shoonya_tick_age_seconds,
                now=now,
            )
            self._decision = decision
        return decision

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            decision = self._decision
            return {
                "active_provider": decision.active_provider if decision else "angel_one",
                "mode": decision.mode if decision else "PRIMARY",
                "reason": decision.reason if decision else "startup_primary",
                "latest_record_count": len(self._latest),
                "forwarded_ticks": self._forwarded,
                "suppressed_ticks": self._suppressed,
                "invalid_records": self._invalid_records,
                "callback_errors": self._callback_errors,
            }


__all__ = ["MarketDataFailoverCoordinator"]
