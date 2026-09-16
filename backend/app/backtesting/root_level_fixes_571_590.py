"""Shared root-level accounting/provenance helpers for backtesting.

This module centralizes semantics used by legacy and event backtests so fixes do
not drift between result/statistics implementations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class ExecutionTrace:
    """Auditable execution-price provenance."""

    market_price: float
    side: str
    slippage: float
    slipped_price: float
    tick_size: float | None
    execution_price: float
    price_source: str


def timestamp_ns(value: Any) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return int(value)
    raise ValueError("timestamp must be date, datetime, or finite numeric nanoseconds")


def cagr(initial_capital: float, final_capital: float, start: Any, end: Any) -> float | None:
    """Calculate CAGR over the actual supplied observation interval."""
    if not isfinite(initial_capital) or not isfinite(final_capital) or initial_capital <= 0 or final_capital <= 0:
        return None
    elapsed_ns = timestamp_ns(end) - timestamp_ns(start)
    if elapsed_ns <= 0:
        return None
    years = elapsed_ns / (365.2425 * 24 * 60 * 60 * 1_000_000_000)
    return (final_capital / initial_capital) ** (1.0 / years) - 1.0


def executable_liquidation_value(cash: float, quantity: float, mark_price: float, *, exit_slippage: float = 0.0, cost_rate: float = 0.0, minimum_cost: float = 0.0) -> tuple[float, float]:
    """Return cash after executable liquidation and its net P&L contribution."""
    if quantity < 0 or mark_price <= 0:
        raise ValueError("quantity and mark_price must be non-negative/positive")
    exit_price = mark_price * (1.0 - exit_slippage)
    gross = exit_price * quantity
    costs = max(minimum_cost, (mark_price * quantity + gross) * cost_rate)
    net = gross - costs
    if not all(isfinite(x) for x in (exit_price, gross, costs, net)):
        raise ValueError("liquidation values must be finite")
    return cash + net, net


def event_identity(record: Any) -> tuple[str, str, str, int, int | None]:
    """Canonical event identity; compatible with HistoricalRecord.identity()."""
    return (record.source, record.instrument, record.timeframe, int(record.timestamp_ns), record.sequence)


def event_sort_key(record: Any) -> tuple[int, int, str, str, str]:
    """Deterministic ordering for merged multi-stream event replay."""
    sequence = record.sequence if record.sequence is not None else -1
    return (int(record.timestamp_ns), sequence, record.source, record.instrument, record.timeframe)


def resolve_signal_price(decision: Any, payload: Mapping[str, Any], price_field: str) -> tuple[float, str]:
    """Resolve price while retaining whether it came from signal or market payload."""
    if not isinstance(price_field, str) or not price_field.strip():
        raise ValueError("price_field must be a non-empty string")
    if getattr(decision, "price", None) is not None:
        value = float(decision.price)
        source = "signal"
    else:
        if price_field not in payload:
            raise ValueError(f"event payload missing price_field: {price_field}")
        value = float(payload[price_field])
        source = f"payload:{price_field}"
    if not isfinite(value) or value <= 0:
        raise ValueError("event execution price must be finite and positive")
    return value, source
