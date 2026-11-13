"""Broker-neutral, shadow-only quote quality comparison primitives.

This module deliberately does not select an executable broker quote or perform
failover. It provides deterministic validation evidence for later review.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .provider_contracts import CanonicalContractKey


class QuoteQualityState(str, Enum):
    COMPARABLE = "COMPARABLE"
    SINGLE_SOURCE_FRESH = "SINGLE_SOURCE_FRESH"
    BOTH_STALE = "BOTH_STALE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    NO_VALID_QUOTE = "NO_VALID_QUOTE"


@dataclass(frozen=True)
class ProviderQuoteObservation:
    provider: str
    contract: CanonicalContractKey
    generation: int
    authenticated: bool
    connection_current: bool
    exchange_timestamp_ns: int | None
    received_monotonic_ns: int
    bid: Any
    ask: Any
    bid_size: Any
    ask_size: Any
    depth_complete: bool = False


@dataclass(frozen=True)
class QuoteQualityResult:
    provider: str
    valid: bool
    fresh: bool
    reasons: tuple[str, ...]
    age_ns: int | None


@dataclass(frozen=True)
class QuoteParityResult:
    state: QuoteQualityState
    left: QuoteQualityResult
    right: QuoteQualityResult
    identity_match: bool


def _positive_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def assess_quote(
    observation: ProviderQuoteObservation,
    *,
    now_monotonic_ns: int,
    max_age_ns: int,
) -> QuoteQualityResult:
    """Validate one quote without mutating or ranking its source."""
    if isinstance(now_monotonic_ns, bool) or not isinstance(now_monotonic_ns, int) or now_monotonic_ns < 0:
        raise ValueError("now_monotonic_ns must be a non-negative integer")
    if isinstance(max_age_ns, bool) or not isinstance(max_age_ns, int) or max_age_ns <= 0:
        raise ValueError("max_age_ns must be a positive integer")

    reasons: list[str] = []
    if not observation.provider.strip():
        reasons.append("provider_missing")
    if isinstance(observation.generation, bool) or observation.generation <= 0:
        reasons.append("invalid_generation")
    if not observation.authenticated:
        reasons.append("session_not_authenticated")
    if not observation.connection_current:
        reasons.append("connection_generation_not_current")

    timestamp = observation.exchange_timestamp_ns
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        reasons.append("exchange_timestamp_missing")
        age_ns = None
    elif timestamp > now_monotonic_ns + 86_400_000_000_000:
        # Exchange epoch timestamps and monotonic clocks have different origins;
        # only compare local monotonic receive age here. This guard is retained
        # for malformed/future numeric values, not as an epoch-clock comparison.
        age_ns = None
        reasons.append("invalid_exchange_timestamp")
    else:
        age_ns = now_monotonic_ns - observation.received_monotonic_ns
        if age_ns < 0:
            reasons.append("receive_time_in_future")
            age_ns = None
        elif age_ns > max_age_ns:
            reasons.append("quote_stale")

    bid = _positive_decimal(observation.bid)
    ask = _positive_decimal(observation.ask)
    bid_size = _positive_decimal(observation.bid_size)
    ask_size = _positive_decimal(observation.ask_size)
    if bid is None:
        reasons.append("invalid_bid")
    if ask is None:
        reasons.append("invalid_ask")
    if bid is not None and ask is not None and bid > ask:
        reasons.append("crossed_market")
    if bid_size is None:
        reasons.append("invalid_bid_size")
    if ask_size is None:
        reasons.append("invalid_ask_size")

    valid = not any(reason not in {"quote_stale"} for reason in reasons)
    fresh = valid and age_ns is not None and age_ns <= max_age_ns
    return QuoteQualityResult(
        provider=observation.provider,
        valid=valid,
        fresh=fresh,
        reasons=tuple(reasons),
        age_ns=age_ns,
    )


def compare_provider_quotes(
    left: ProviderQuoteObservation,
    right: ProviderQuoteObservation,
    *,
    now_monotonic_ns: int,
    max_age_ns: int,
) -> QuoteParityResult:
    """Compare two provider observations for the same canonical instrument.

    This is diagnostic only: it never chooses a source or creates a synthetic
    bid/ask by mixing fields from different providers.
    """
    left_result = assess_quote(left, now_monotonic_ns=now_monotonic_ns, max_age_ns=max_age_ns)
    right_result = assess_quote(right, now_monotonic_ns=now_monotonic_ns, max_age_ns=max_age_ns)
    identity_match = left.contract == right.contract
    if not identity_match:
        state = QuoteQualityState.IDENTITY_MISMATCH
    elif left_result.fresh and right_result.fresh:
        state = QuoteQualityState.COMPARABLE
    elif left_result.fresh or right_result.fresh:
        state = QuoteQualityState.SINGLE_SOURCE_FRESH
    elif left_result.valid or right_result.valid:
        state = QuoteQualityState.BOTH_STALE
    else:
        state = QuoteQualityState.NO_VALID_QUOTE
    return QuoteParityResult(state, left_result, right_result, identity_match)
