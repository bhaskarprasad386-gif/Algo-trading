"""Unified historical replay entry point for executable arbitrage strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.execution.payoff import PayoffLeg

from .arbitrage_backtest_suite import build_strategy_adapter
from .backtest_result import BacktestRunWriter, PayoffSnapshot
from .backtest_run import BacktestRunSpec
from .historical_arbitrage_runner import HistoricalArbitrageRunner
from .result_ledger import BacktestResultLedger


@dataclass(frozen=True)
class HistoricalArbitrageBacktestResult:
    completed_trades: int
    payoff: PayoffSnapshot | None


def _quote(event: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = event.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} quote payload is required")
    return value


def _payoff_legs(strategy_id: str, event: Mapping[str, Any], direction: str, quantity: float) -> tuple[PayoffLeg, ...]:
    if strategy_id == "box-spread":
        low, high = _quote(event, "low"), _quote(event, "high")
        long = direction == "LONG"
        return (
            PayoffLeg("CALL", "BUY" if long else "SELL", float(low["strike"]), float(low["call_ask"] if long else low["call_bid"]), quantity),
            PayoffLeg("PUT", "BUY" if long else "SELL", float(low["strike"]), float(low["put_ask"] if long else low["put_bid"]), quantity),
            PayoffLeg("CALL", "SELL" if long else "BUY", float(high["strike"]), float(high["call_bid"] if long else high["call_ask"]), quantity),
            PayoffLeg("PUT", "SELL" if long else "BUY", float(high["strike"]), float(high["put_bid"] if long else high["put_ask"]), quantity),
        )
    if strategy_id == "synthetic-cash-carry":
        option, future = _quote(event, "option"), _quote(event, "future")
        long = direction == "LONG"
        return (
            PayoffLeg("CALL", "BUY" if long else "SELL", float(option["strike"]), float(option["call_ask"] if long else option["call_bid"]), quantity),
            PayoffLeg("PUT", "SELL" if long else "BUY", float(option["strike"]), float(option["put_bid"] if long else option["put_ask"]), quantity),
            PayoffLeg("FUTURE", "SELL" if long else "BUY", None, float(future["bid"] if long else future["ask"]), quantity),
        )
    if strategy_id == "cash-future":
        q = _quote(event, "cash_future")
        long = direction == "LONG_CASH_SHORT_FUTURE"
        return (
            PayoffLeg("SPOT", "BUY" if long else "SELL", None, float(q["spot_ask"] if long else q["spot_bid"]), quantity),
            PayoffLeg("FUTURE", "SELL" if long else "BUY", None, float(q["future_bid"] if long else q["future_ask"]), quantity),
        )
    if strategy_id == "calendar-spread":
        near, far = _quote(event, "near"), _quote(event, "far")
        long = direction == "LONG_NEAR_SHORT_FAR"
        kind = "CALL" if near.get("option_type") == "CALL" else "PUT" if near.get("option_type") == "PUT" else "FUTURE"
        strike = float(near["strike"]) if kind in {"CALL", "PUT"} else None
        return (
            PayoffLeg(kind, "BUY" if long else "SELL", strike, float(near["ask"] if long else near["bid"]), quantity),
            PayoffLeg(kind, "SELL" if long else "BUY", strike, float(far["bid"] if long else far["ask"]), quantity),
        )
    raise ValueError(f"unsupported arbitrage strategy: {strategy_id}")


def run_historical_arbitrage(
    ledger: BacktestResultLedger,
    spec: BacktestRunSpec,
    events: Iterable[Mapping[str, Any]],
    *,
    payoff_prices: tuple[float, ...] = (),
) -> HistoricalArbitrageBacktestResult:
    """Run one isolated strategy replay; results are persisted incrementally.

    The input iterable is streamed directly into the runner. Payoff legs are
    derived from the first real executable entry, never from fabricated prices.
    """
    writer = BacktestRunWriter(ledger, spec)
    adapter = build_strategy_adapter(spec.strategy_id, spec.parameters)
    runner = HistoricalArbitrageRunner(writer)
    payoff_state: list[PayoffSnapshot | None] = [None]
    payoff_written = [False]

    def entry(event: Mapping[str, Any]):
        positions = tuple(adapter.entry(event))
        if positions and payoff_prices and not payoff_written[0]:
            payoff_state[0] = writer.record_payoff(
                0,
                int(event["timestamp_ns"]),
                _payoff_legs(spec.strategy_id, event, str(spec.parameters.get("direction", positions[0].side)), positions[0].quantity),
                tuple(float(price) for price in payoff_prices),
            )
            payoff_written[0] = True
        return positions

    completed = runner.replay(events, entry, adapter.exit)
    return HistoricalArbitrageBacktestResult(completed, payoff_state[0])


__all__ = ["HistoricalArbitrageBacktestResult", "run_historical_arbitrage"]
