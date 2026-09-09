"""Unified orchestration for independent historical arbitrage backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from app.execution.payoff import PayoffLeg

from .arbitrage_backtest_suite import build_strategy_adapter
from .backtest_result import BacktestRunWriter, PayoffSnapshot
from .historical_arbitrage_runner import ExitExecution, HistoricalArbitrageRunner, OpenPosition


EntrySelector = Callable[[Mapping[str, Any]], Iterable[OpenPosition]]
ExitSelector = Callable[[OpenPosition, Mapping[str, Any]], ExitExecution | None]


@dataclass(frozen=True)
class HistoricalArbitrageBacktestResult:
    """Small immutable result handle; detailed trades remain in the ledger."""
    run_id: str
    completed_trades: int
    unresolved_trades: int
    realized_pnl: float
    payoff: PayoffSnapshot | None


class HistoricalArbitrageBacktestService:
    """Run a registered arbitrage strategy through one audited lifecycle."""

    def __init__(self, writer: BacktestRunWriter) -> None:
        self.writer = writer

    def run(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        entry_selector: EntrySelector,
        exit_selector: ExitSelector,
        payoff_legs: tuple[PayoffLeg, ...] = (),
        payoff_prices: tuple[float, ...] = (),
        payoff_sequence: int | None = None,
        payoff_timestamp_ns: int | None = None,
        equity_selector: Callable[[Mapping[str, Any], float], Any] | None = None,
    ) -> HistoricalArbitrageBacktestResult:
        runner = HistoricalArbitrageRunner(self.writer)
        runner.replay(events, entry_selector, exit_selector, equity_selector=equity_selector)
        payoff: PayoffSnapshot | None = None
        if payoff_legs:
            if not payoff_prices:
                raise ValueError("payoff_prices are required when payoff_legs are supplied")
            sequence = runner.audit_sequence + 1 if payoff_sequence is None else payoff_sequence
            timestamp_ns = self.writer.spec.end_ns if payoff_timestamp_ns is None else payoff_timestamp_ns
            payoff = self.writer.record_payoff(sequence, timestamp_ns, payoff_legs, payoff_prices)
        return HistoricalArbitrageBacktestResult(
            run_id=self.writer.spec.run_id,
            completed_trades=runner.completed,
            unresolved_trades=len(runner.open_positions),
            realized_pnl=runner.realized_pnl,
            payoff=payoff,
        )

    def run_strategy(
        self,
        strategy_id: str,
        events: Iterable[Mapping[str, Any]],
        *,
        parameters: Mapping[str, Any] | None = None,
        payoff_legs: tuple[PayoffLeg, ...] = (),
        payoff_prices: tuple[float, ...] = (),
        payoff_sequence: int | None = None,
        payoff_timestamp_ns: int | None = None,
        equity_selector: Callable[[Mapping[str, Any], float], Any] | None = None,
    ) -> HistoricalArbitrageBacktestResult:
        """Build a registered adapter and replay it without strategy fallback."""
        adapter = build_strategy_adapter(strategy_id, parameters)
        return self.run(
            events,
            entry_selector=adapter.entry,
            exit_selector=adapter.exit,
            payoff_legs=payoff_legs,
            payoff_prices=payoff_prices,
            payoff_sequence=payoff_sequence,
            payoff_timestamp_ns=payoff_timestamp_ns,
            equity_selector=equity_selector,
        )


__all__ = ["HistoricalArbitrageBacktestResult", "HistoricalArbitrageBacktestService"]
