"""Read-only per-contract source freshness and quote parity for shared index feeds."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import monotonic_ns
from typing import Iterable

from .provider_contracts import CanonicalContractKey, ContractMapping


@dataclass(frozen=True)
class QuoteSourceObservation:
    broker: str
    canonical_key: CanonicalContractKey
    ltp: float | None
    bid: float | None
    ask: float | None
    broker_exchange: str
    broker_segment: str
    broker_token: str
    exchange_timestamp_ns: int
    received_monotonic_ns: int
    feed_epoch: int
    authenticated: bool


@dataclass(frozen=True)
class QuoteParityStatus:
    canonical_key: CanonicalContractKey
    state: str
    angel_age_ms: float | None
    shoonya_age_ms: float | None
    ltp_delta: float | None
    tolerance: float | None
    sources_fresh: tuple[str, ...]


class SharedIndexQuoteParity:
    """Never mixes broker fields; tracks source observations independently.

    This class is intentionally a read-only observer and has no subscription,
    failover, trading, or scanner side effects.
    """
    LOCKED_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY"})
    SOURCES = frozenset({"ANGELONE", "SHOONYA"})

    def __init__(self, mappings: Iterable[ContractMapping], *, stale_after_ms: int = 2500):
        if stale_after_ms < 1:
            raise ValueError("stale_after_ms must be positive")
        self._lock = RLock()
        self._stale_ns = int(stale_after_ms) * 1_000_000
        self._tick_sizes: dict[CanonicalContractKey, float] = {}
        self._source_tokens: dict[tuple[CanonicalContractKey, str], tuple[str, str]] = {}
        self._observations: dict[tuple[CanonicalContractKey, str], QuoteSourceObservation] = {}
        for item in tuple(mappings):
            key = item.key
            if key.underlying not in self.LOCKED_UNDERLYINGS:
                continue
            if not item.tradable:
                continue
            broker = item.provider.broker.upper()
            if broker not in self.SOURCES:
                continue
            lookup = (key, broker)
            if lookup in self._source_tokens:
                raise ValueError(f"duplicate source mapping for canonical contract: {key} {broker}")
            self._source_tokens[lookup] = (item.provider.exchange, item.provider.segment, item.provider.token)
            self._tick_sizes[key] = float(item.tick_size)

    def observe(self, observation: QuoteSourceObservation) -> QuoteParityStatus | None:
        broker = observation.broker.upper()
        if broker not in self.SOURCES or observation.canonical_key.underlying not in self.LOCKED_UNDERLYINGS:
            return None
        lookup = (observation.canonical_key, broker)
        expected = self._source_tokens.get(lookup)
        # Broker token and exchange must match the published master mapping.
        if expected is None or expected != (observation.broker_exchange.upper(), observation.broker_segment.upper(), observation.broker_token):
            # canonical segment/exchange validation is performed by caller's
            # mapping lookup; fail closed rather than accept unknown instruments.
            return None
        if observation.feed_epoch < 0 or not observation.authenticated or observation.received_monotonic_ns <= 0:
            return None
        with self._lock:
            old = self._observations.get(lookup)
            if old and observation.feed_epoch == old.feed_epoch and observation.exchange_timestamp_ns < old.exchange_timestamp_ns:
                return self._status_locked(observation.canonical_key, observation.received_monotonic_ns)
            self._observations[lookup] = observation
            return self._status_locked(observation.canonical_key, observation.received_monotonic_ns)

    def _status_locked(self, key: CanonicalContractKey, now_ns: int) -> QuoteParityStatus:
        ages = {}
        fresh = []
        quotes = {}
        for source in sorted(self.SOURCES):
            obs = self._observations.get((key, source))
            age = None if obs is None else max(0, now_ns - obs.received_monotonic_ns) / 1e6
            ages[source] = age
            if obs is not None and age is not None and age * 1e6 <= self._stale_ns and obs.authenticated:
                fresh.append(source)
                if obs.ltp is not None and obs.ltp > 0:
                    quotes[source] = obs.ltp
        delta = abs(quotes["ANGELONE"] - quotes["SHOONYA"]) if len(quotes) == 2 else None
        tolerance = self._tick_sizes.get(key)
        if len(fresh) < 2:
            state = "SINGLE_SOURCE_FRESH" if fresh else "BOTH_STALE"
        elif delta is None:
            state = "BOTH_FRESH_NO_LTP"
        elif tolerance is not None and delta <= tolerance:
            state = "PARITY_OK"
        else:
            state = "PARITY_MISMATCH"
        return QuoteParityStatus(key, state, ages["ANGELONE"], ages["SHOONYA"], delta, tolerance, tuple(fresh))

    def status(self, key: CanonicalContractKey, *, now_monotonic_ns: int | None = None) -> QuoteParityStatus:
        with self._lock:
            return self._status_locked(key, now_monotonic_ns or monotonic_ns())
