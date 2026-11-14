"""Read-only bridge from broker callback evidence to readiness reducers.

No quote is forwarded to scanners, pairers, persistence, or order execution.
"""
from __future__ import annotations

from threading import RLock
from time import monotonic_ns, time_ns
from typing import Any

from .provider_readiness import ProviderReadiness


class ProviderReadinessBridge:
    def __init__(self) -> None:
        self._lock = RLock()
        self._shoonya = ProviderReadiness("shoonya", subscription_ack_supported=False)
        self._shoonya_generation = 0
        self._angel: dict[str, ProviderReadiness] = {}
        self._angel_generations: dict[str, int] = {}

    @staticmethod
    def _valid_record(record: Any) -> tuple[bool, bool, bool]:
        instrument = getattr(record, "instrument", None)
        identity = bool(
            instrument is not None
            and str(getattr(instrument, "exchange", "")).strip()
            and str(getattr(instrument, "segment", "")).strip()
            and str(getattr(instrument, "token", "")).strip()
            and str(getattr(record, "symbol", "")).strip()
        )
        timestamp = getattr(record, "timestamp_ns", None)
        timestamp_valid = isinstance(timestamp, int) and not isinstance(timestamp, bool) and timestamp > 0
        fresh = bool(timestamp_valid and abs(time_ns() - timestamp) <= 5_000_000_000)
        return identity, timestamp_valid, fresh

    def shoonya_state(
        self, state: str, reason: str | None, generation: int,
        connected: bool, authenticated: bool, received_monotonic_ns: int,
    ) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            return
        with self._lock:
            if generation > self._shoonya_generation:
                self._shoonya.begin_generation(generation)
                self._shoonya_generation = generation
            current = self._shoonya_generation
            if generation != current:
                return
            if state == "AUTHENTICATING":
                self._shoonya.transport_open(current)
            elif state == "LIVE" and authenticated:
                self._shoonya.transport_open(current)
                self._shoonya.record_authentication_ack(
                    current, accepted=True, evidence_kind=ProviderReadiness.AUTH_ACK_KIND
                )
            elif state == "AUTH_FAILED":
                self._shoonya.transport_open(current)
                self._shoonya.record_authentication_ack(
                    current, accepted=False, evidence_kind=ProviderReadiness.AUTH_ACK_KIND
                )
            elif state == "AUTH_TIMEOUT":
                self._shoonya.authentication_timeout(current)
                self._shoonya.transport_closed(current, reason=state)
            elif state in {"RECONNECTING", "DISCONNECTED", "STOPPED", "ERROR", "DEGRADED", "STOP_TIMEOUT"}:
                self._shoonya.transport_closed(current, reason=state)
            elif connected:
                self._shoonya.transport_open(current)

    def shoonya_market_event(self, record: Any, generation: int, authenticated: bool, received_at: int) -> None:
        with self._lock:
            if generation != self._shoonya_generation or not authenticated:
                return
            identity, timestamp_valid, fresh = self._valid_record(record)
            self._shoonya.record_market_event(
                generation, canonical_identity_valid=identity,
                exchange_timestamp_valid=timestamp_valid, fresh=fresh,
                received_monotonic_ns=received_at,
            )

    def angel_market_event(
        self, record: Any, group: Any, generation: int, authenticated: bool,
        connection_current: bool, received_at: int,
    ) -> None:
        # SmartAPI currently has no verified auth ACK. Ignore the callback's
        # auth-shaped value unless a future reviewed adapter changes the contract.
        if not connection_current:
            return
        key = f"{getattr(group, 'mode', 'unknown')}:{getattr(group, 'shard', 'unknown')}"
        with self._lock:
            current = self._angel_generations.get(key, 0)
            if generation > current:
                state = ProviderReadiness("angel_one", subscription_ack_supported=False)
                state.begin_generation(generation)
                self._angel[key] = state
                self._angel_generations[key] = generation
            state = self._angel.get(key)
            if state is None or self._angel_generations.get(key) != generation:
                return
            state.transport_open(generation)
            identity, timestamp_valid, fresh = self._valid_record(record)
            state.record_market_event(
                generation, canonical_identity_valid=identity,
                exchange_timestamp_valid=timestamp_valid, fresh=fresh,
                received_monotonic_ns=received_at,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": "read_only_diagnostics",
                "shoonya": self._shoonya.snapshot(),
                "angel_one": {
                    key: state.snapshot() for key, state in sorted(self._angel.items())
                },
                "pairing_enabled": False,
                "scanner_fanout_enabled": False,
                "order_eligible": False,
            }
