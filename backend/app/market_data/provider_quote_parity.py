"""Instrument-wise shadow quote parity metrics; never selects an execution source."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from collections import deque
from threading import RLock
from typing import Mapping

from .provider_contracts import CanonicalContractKey

from .provider_quote_quality import (
    ProviderQuoteObservation, QuoteParityResult, compare_provider_quotes, _positive_decimal,
)


@dataclass(frozen=True)
class QuoteParityMetrics:
    contract: CanonicalContractKey
    quality: QuoteParityResult
    bid_abs_diff: Decimal | None
    ask_abs_diff: Decimal | None
    bid_relative_diff_bps: Decimal | None
    ask_relative_diff_bps: Decimal | None
    bid_size_abs_diff: Decimal | None
    ask_size_abs_diff: Decimal | None
    mid_abs_diff: Decimal | None
    mid_relative_diff_bps: Decimal | None
    exchange_timestamp_skew_ns: int | None


def _absolute_difference(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return abs(left - right)


def _relative_bps(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    denominator = (abs(left) + abs(right)) / Decimal(2)
    if denominator <= 0:
        return None
    return abs(left - right) / denominator * Decimal(10_000)


def analyze_quote_parity(
    left: ProviderQuoteObservation,
    right: ProviderQuoteObservation,
    *,
    now_monotonic_ns: int,
    now_exchange_timestamp_ns: int,
    max_age_ns: int,
) -> QuoteParityMetrics:
    """Measure provider divergence only for an identical canonical contract.

    Metrics may be present for stale-but-structurally-valid quotes for diagnosis.
    They are always absent for identity mismatch; no side is ever selected.
    """
    quality = compare_provider_quotes(
        left, right, now_monotonic_ns=now_monotonic_ns,
        now_exchange_timestamp_ns=now_exchange_timestamp_ns, max_age_ns=max_age_ns,
    )
    if not quality.identity_match:
        return QuoteParityMetrics(left.contract, quality, None, None, None, None, None, None, None, None, None)

    left_bid, right_bid = _positive_decimal(left.bid), _positive_decimal(right.bid)
    left_ask, right_ask = _positive_decimal(left.ask), _positive_decimal(right.ask)
    left_bid_size, right_bid_size = _positive_decimal(left.bid_size), _positive_decimal(right.bid_size)
    left_ask_size, right_ask_size = _positive_decimal(left.ask_size), _positive_decimal(right.ask_size)
    left_mid = (left_bid + left_ask) / 2 if left_bid is not None and left_ask is not None else None
    right_mid = (right_bid + right_ask) / 2 if right_bid is not None and right_ask is not None else None
    left_ts, right_ts = left.exchange_timestamp_ns, right.exchange_timestamp_ns
    skew = abs(left_ts - right_ts) if left_ts and right_ts else None
    return QuoteParityMetrics(
        contract=left.contract,
        quality=quality,
        bid_abs_diff=_absolute_difference(left_bid, right_bid),
        ask_abs_diff=_absolute_difference(left_ask, right_ask),
        bid_relative_diff_bps=_relative_bps(left_bid, right_bid),
        ask_relative_diff_bps=_relative_bps(left_ask, right_ask),
        bid_size_abs_diff=_absolute_difference(left_bid_size, right_bid_size),
        ask_size_abs_diff=_absolute_difference(left_ask_size, right_ask_size),
        mid_abs_diff=_absolute_difference(left_mid, right_mid),
        mid_relative_diff_bps=_relative_bps(left_mid, right_mid),
        exchange_timestamp_skew_ns=skew,
    )


class QuoteParityHistory:
    """Thread-safe bounded in-memory diagnostics; never used for execution."""

    def __init__(self, *, max_observations_per_contract: int = 300) -> None:
        if isinstance(max_observations_per_contract, bool) or not isinstance(max_observations_per_contract, int) or max_observations_per_contract <= 0:
            raise ValueError("max_observations_per_contract must be a positive integer")
        self._capacity = max_observations_per_contract
        self._lock = RLock()
        self._samples: dict[CanonicalContractKey, deque[tuple[int, QuoteParityMetrics]]] = {}

    def record(self, metrics: QuoteParityMetrics, *, observed_monotonic_ns: int) -> int:
        if not isinstance(metrics, QuoteParityMetrics):
            raise TypeError("metrics must be QuoteParityMetrics")
        if isinstance(observed_monotonic_ns, bool) or not isinstance(observed_monotonic_ns, int) or observed_monotonic_ns < 0:
            raise ValueError("observed_monotonic_ns must be a non-negative integer")
        with self._lock:
            samples = self._samples.setdefault(metrics.contract, deque(maxlen=self._capacity))
            if samples and observed_monotonic_ns < samples[-1][0]:
                raise ValueError("history timestamps must be monotonic per contract")
            samples.append((observed_monotonic_ns, metrics))
            return len(samples)

    def snapshot(self) -> Mapping[CanonicalContractKey, tuple[tuple[int, QuoteParityMetrics], ...]]:
        with self._lock:
            return {key: tuple(values) for key, values in self._samples.items()}

    def clear(self) -> int:
        with self._lock:
            count = sum(len(values) for values in self._samples.values())
            self._samples.clear()
            return count
