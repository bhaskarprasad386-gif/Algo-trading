"""Bounded, generation-fenced pairing of normalized Angel/Shoonya quotes.

This component is deliberately transport-agnostic. Callers must supply trusted
session metadata and monotonic receive time; it never opens sockets, subscribes
instruments, selects a broker, or routes orders.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from time import time_ns
from typing import Callable

from .provider_quote_quality import ProviderQuoteObservation
from .provider_quote_parity_worker import BoundedQuoteParityWorker, QuoteParityEnvelope


@dataclass(frozen=True)
class _PendingQuote:
    observation: ProviderQuoteObservation
    observed_at_utc_ns: int
    now_exchange_timestamp_ns: int
    max_age_ns: int


class ProviderQuotePairer:
    """Pair the latest opposite-provider observation for the same contract.

    A pair is submitted once only after the worker accepts it. Generation
    changes discard that provider's pending quotes. Memory is bounded by
    max_contracts; stale and unauthenticated records are rejected upstream by
    the quote-quality evaluator in the worker.
    """

    PROVIDERS = frozenset({"angel_one", "shoonya"})

    def __init__(
        self,
        worker: BoundedQuoteParityWorker,
        *,
        max_contracts: int = 4096,
        clock_ns: Callable[[], int] = time_ns,
    ) -> None:
        if not isinstance(worker, BoundedQuoteParityWorker):
            raise TypeError("worker must be BoundedQuoteParityWorker")
        if isinstance(max_contracts, bool) or not isinstance(max_contracts, int) or max_contracts < 1:
            raise ValueError("max_contracts must be a positive integer")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")
        self._worker = worker
        self._capacity = max_contracts
        self._clock_ns = clock_ns
        self._lock = RLock()
        self._generations: dict[str, int] = {}
        self._pending: OrderedDict[tuple[object, str], _PendingQuote] = OrderedDict()
        self._last_emitted: OrderedDict[tuple[object, tuple[tuple[str, int, int], ...]], None] = OrderedDict()
        self._counters = {
            "accepted_observations": 0,
            "rejected_provider": 0,
            "rejected_generation": 0,
            "rejected_untrusted": 0,
            "pairs_submitted": 0,
            "worker_rejected_pairs": 0,
            "evicted_contracts": 0,
        }

    def activate_generation(self, provider: str, generation: int) -> None:
        if provider not in self.PROVIDERS:
            raise ValueError("unsupported provider")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("generation must be a positive integer")
        with self._lock:
            previous = self._generations.get(provider)
            if previous is not None and generation <= previous:
                raise ValueError("generation must strictly increase")
            self._generations[provider] = generation
            for key in tuple(self._pending):
                if key[1] == provider:
                    self._pending.pop(key, None)
            self._worker.activate_generation(provider, generation)

    def submit(
        self,
        observation: ProviderQuoteObservation,
        *,
        received_monotonic_ns: int,
        max_age_ns: int,
    ) -> bool:
        if not isinstance(observation, ProviderQuoteObservation):
            raise TypeError("observation must be ProviderQuoteObservation")
        if isinstance(received_monotonic_ns, bool) or not isinstance(received_monotonic_ns, int) or received_monotonic_ns <= 0:
            raise ValueError("received_monotonic_ns must be a positive integer")
        if isinstance(max_age_ns, bool) or not isinstance(max_age_ns, int) or max_age_ns <= 0:
            raise ValueError("max_age_ns must be a positive integer")
        provider = observation.provider
        if provider not in self.PROVIDERS:
            with self._lock:
                self._counters["rejected_provider"] += 1
            return False
        if not observation.authenticated or not observation.connection_current:
            with self._lock:
                self._counters["rejected_untrusted"] += 1
            return False
        with self._lock:
            if self._generations.get(provider) != observation.generation:
                self._counters["rejected_generation"] += 1
                return False
            self._counters["accepted_observations"] += 1
            own_key = (observation.contract, provider)
            other_provider = "shoonya" if provider == "angel_one" else "angel_one"
            other_key = (observation.contract, other_provider)
            pending = _PendingQuote(
                observation=observation,
                observed_at_utc_ns=self._clock_ns(),
                now_exchange_timestamp_ns=self._clock_ns(),
                max_age_ns=max_age_ns,
            )
            self._pending[own_key] = pending
            self._pending.move_to_end(own_key)
            while len(self._pending) > self._capacity * 2:
                self._pending.popitem(last=False)
                self._counters["evicted_contracts"] += 1
            counterpart = self._pending.get(other_key)
            if counterpart is None:
                return True
            left, right = (counterpart, pending) if other_provider == "angel_one" else (pending, counterpart)
            identity = tuple(sorted((
                (left.observation.provider, left.observation.generation,
                 left.observation.exchange_timestamp_ns or left.observation.received_monotonic_ns),
                (right.observation.provider, right.observation.generation,
                 right.observation.exchange_timestamp_ns or right.observation.received_monotonic_ns),
            )))
            pair_key = (observation.contract, identity)
            if pair_key in self._last_emitted:
                return True
            envelope = QuoteParityEnvelope(
                left=left.observation,
                right=right.observation,
                received_monotonic_ns=max(
                    left.observation.received_monotonic_ns,
                    right.observation.received_monotonic_ns,
                ),
                observed_at_utc_ns=max(left.observed_at_utc_ns, right.observed_at_utc_ns),
                now_exchange_timestamp_ns=max(
                    left.now_exchange_timestamp_ns, right.now_exchange_timestamp_ns,
                ),
                max_age_ns=min(left.max_age_ns, right.max_age_ns),
            )
            if not self._worker.submit(envelope):
                self._counters["worker_rejected_pairs"] += 1
                return False
            self._last_emitted[pair_key] = None
            self._last_emitted.move_to_end(pair_key)
            while len(self._last_emitted) > self._capacity * 4:
                self._last_emitted.popitem(last=False)
            self._counters["pairs_submitted"] += 1
            return True

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                **self._counters,
                "pending_quotes": len(self._pending),
                "pending_capacity": self._capacity * 2,
                "emitted_pair_keys": len(self._last_emitted),
                "active_generations": dict(self._generations),
            }
