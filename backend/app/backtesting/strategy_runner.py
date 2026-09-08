"""Generic strategy adapter for the deterministic backtesting engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from app.algo.strategy import Strategy
from app.backtesting.engine import BacktestConfig, BacktestEngine, BacktestResult


class StrategyProvider(Protocol):
    """Optional richer strategy contract for stateful/event-aware strategies."""

    def on_candle(self, context: Mapping[str, float]) -> tuple[bool, bool]: ...


class GenericStrategyRunner:
    """Run arbitrary entry/exit strategies through one shared backtest engine.

    A strategy remains a plug-in: the engine owns replay, execution economics,
    portfolio accounting and result metrics. No strategy-specific engine is
    created here.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.engine = BacktestEngine(config)

    def run(
        self,
        candles: Iterable[Mapping[str, object]],
        *,
        entry: Strategy,
        exit: Strategy,
    ) -> BacktestResult:
        return self.engine.run(candles, entry_strategy=entry, exit_strategy=exit)

    def run_incremental(
        self,
        candles: Iterable[Mapping[str, object]],
        *,
        entry: Strategy,
        exit: Strategy,
        persist_chunk,
        chunk_size: int = 500,
    ) -> BacktestResult:
        return self.engine.run_incremental(
            candles,
            entry_strategy=entry,
            exit_strategy=exit,
            persist_chunk=persist_chunk,
            chunk_size=chunk_size,
        )
