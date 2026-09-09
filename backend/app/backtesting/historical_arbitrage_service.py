"""Unified orchestration for independent historical arbitrage backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from app.execution.payoff import PayoffLeg

from .arbitrage_backtest_suite import build_strategy_adapter
from .arbitrage_payoff import build_strategy_payoff
from .backtest_result import BacktestRunWriter, PayoffSnapshot
from .historical_arbitrage_runner import ExitExecution, HistoricalArbitrageRunner, OpenPosition
from .historical_catalog import HistoricalCatalog
from .historical_catalog_replay import CatalogReplayLeg, HistoricalCatalogEventReplay
from .historical_chain_quote_bridge import normalize_chain_payload

EntrySelector = Callable[[Mapping[str, Any]], Iterable[OpenPosition]]
ExitSelector = Callable[[OpenPosition, Mapping[str, Any]], ExitExecution | None]


@dataclass(frozen=True)
class HistoricalArbitrageBacktestResult:
    run_id: str
    completed_trades: int
    unresolved_trades: int
    realized_pnl: float
    payoff: PayoffSnapshot | None


class HistoricalArbitrageBacktestService:
    """Run a registered arbitrage strategy through one audited lifecycle."""

    def __init__(self, writer: BacktestRunWriter) -> None:
        self.writer = writer

    def run(self, events: Iterable[Mapping[str, Any]], *, entry_selector: EntrySelector, exit_selector: ExitSelector,
            payoff_legs: tuple[PayoffLeg, ...] = (), payoff_prices: tuple[float, ...] = (),
            payoff_sequence: int | None = None, payoff_timestamp_ns: int | None = None,
            equity_selector: Callable[[Mapping[str, Any], float], Any] | None = None) -> HistoricalArbitrageBacktestResult:
        runner = HistoricalArbitrageRunner(self.writer)
        try:
            runner.replay(events, entry_selector, exit_selector, equity_selector=equity_selector, finalize=False)
            payoff: PayoffSnapshot | None = None
            if payoff_legs:
                if not payoff_prices:
                    raise ValueError("payoff_prices are required when payoff_legs are supplied")
                sequence = runner.audit_sequence + 1 if payoff_sequence is None else payoff_sequence
                timestamp_ns = self.writer.spec.end_ns if payoff_timestamp_ns is None else payoff_timestamp_ns
                payoff = self.writer.record_payoff(sequence, timestamp_ns, payoff_legs, payoff_prices)
            runner.finalize()
            return HistoricalArbitrageBacktestResult(self.writer.spec.run_id, runner.completed, runner.open_positions_count, runner.realized_pnl, payoff)
        except Exception as exc:
            if self.writer.ledger.run(self.writer.spec.run_id)["status"] != "FAILED":
                self.writer.fail(str(exc))
            raise

    @staticmethod
    def _normalize_catalog_event(strategy_id: str, event: Mapping[str, Any]) -> Mapping[str, Any]:
        """Normalize only explicit raw chain wrappers; never reinterpret ordinary quote payloads."""
        normalized = dict(event)
        if strategy_id == "box-spread":
            for key in ("low", "high"):
                value = normalized.get(key)
                if isinstance(value, Mapping) and "contracts" in value:
                    normalized[key] = normalize_chain_payload(value, option=True)
        elif strategy_id == "synthetic-cash-carry":
            option = normalized.get("option")
            future = normalized.get("future")
            if isinstance(option, Mapping) and "contracts" in option:
                normalized["option"] = normalize_chain_payload(option, option=True)
            if isinstance(future, Mapping) and "contract" in future:
                normalized["future"] = normalize_chain_payload(future, option=False)
        return normalized

    def run_strategy(self, strategy_id: str, events: Iterable[Mapping[str, Any]], *, parameters: Mapping[str, Any] | None = None,
                     payoff_legs: tuple[PayoffLeg, ...] = (), payoff_prices: tuple[float, ...] = (),
                     payoff_sequence: int | None = None, payoff_timestamp_ns: int | None = None,
                     equity_selector: Callable[[Mapping[str, Any], float], Any] | None = None) -> HistoricalArbitrageBacktestResult:
        """Replay a registered strategy and derive strategy payoff before completion."""
        adapter = build_strategy_adapter(strategy_id, parameters)
        captured: list[Mapping[str, Any]] = []

        def entry(event: Mapping[str, Any]) -> Iterable[OpenPosition]:
            event = self._normalize_catalog_event(strategy_id, event)
            positions = tuple(adapter.entry(event))
            if positions and not captured:
                captured.append(event)
            return positions

        def exit_selector(position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
            return adapter.exit(position, self._normalize_catalog_event(strategy_id, event))

        runner = HistoricalArbitrageRunner(self.writer)
        try:
            runner.replay(events, entry, exit_selector, equity_selector=equity_selector, finalize=False)
            payoff: PayoffSnapshot | None = None
            if payoff_legs:
                if not payoff_prices:
                    raise ValueError("payoff_prices are required when payoff_legs are supplied")
                sequence = runner.audit_sequence + 1 if payoff_sequence is None else payoff_sequence
                timestamp_ns = self.writer.spec.end_ns if payoff_timestamp_ns is None else payoff_timestamp_ns
                payoff = self.writer.record_payoff(sequence, timestamp_ns, payoff_legs, payoff_prices)
            elif payoff_prices and captured:
                strategy_payoff = build_strategy_payoff(strategy_id, captured[0], direction=(parameters or {}).get("direction"))
                sequence = runner.audit_sequence + 1 if payoff_sequence is None else payoff_sequence
                timestamp_ns = self.writer.spec.end_ns if payoff_timestamp_ns is None else payoff_timestamp_ns
                payoff = self.writer.record_payoff(sequence, timestamp_ns, strategy_payoff.legs, payoff_prices)
            runner.finalize()
            return HistoricalArbitrageBacktestResult(self.writer.spec.run_id, runner.completed, runner.open_positions_count, runner.realized_pnl, payoff)
        except Exception as exc:
            if self.writer.ledger.run(self.writer.spec.run_id)["status"] != "FAILED":
                self.writer.fail(str(exc))
            raise

    def run_catalog_strategy(self, strategy_id: str, catalog: HistoricalCatalog, legs: tuple[CatalogReplayLeg, ...], *,
                             start_ns: int | None = None, end_ns: int | None = None,
                             parameters: Mapping[str, Any] | None = None, payoff_legs: tuple[PayoffLeg, ...] = (),
                             payoff_prices: tuple[float, ...] = (), payoff_sequence: int | None = None,
                             payoff_timestamp_ns: int | None = None,
                             equity_selector: Callable[[Mapping[str, Any], float], Any] | None = None) -> HistoricalArbitrageBacktestResult:
        """Run a strategy directly from persistent catalog records using exact timestamps only."""
        replay_start = self.writer.spec.start_ns if start_ns is None else start_ns
        replay_end = self.writer.spec.end_ns if end_ns is None else end_ns
        events = HistoricalCatalogEventReplay(catalog).events(legs, start_ns=replay_start, end_ns=replay_end, require_complete=True)
        return self.run_strategy(strategy_id, events, parameters=parameters, payoff_legs=payoff_legs,
                                 payoff_prices=payoff_prices, payoff_sequence=payoff_sequence,
                                 payoff_timestamp_ns=payoff_timestamp_ns, equity_selector=equity_selector)


__all__ = ["HistoricalArbitrageBacktestResult", "HistoricalArbitrageBacktestService"]
