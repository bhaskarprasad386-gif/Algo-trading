"""Stable contracts for the backtesting core."""

from __future__ import annotations

from typing import Iterable, Mapping, Protocol, runtime_checkable

from app.backtesting.engine import EventContext, EventSignal
from app.backtesting.execution import (
    AtomicExecutionResult,
    ExecutionResult,
    SimFill,
    SimOrder,
    OrderBook,
    QueueEvidence,
)
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.portfolio import PortfolioSnapshot


@runtime_checkable
class StrategyProtocol(Protocol):
    def __call__(self, context: EventContext) -> EventSignal | str | None:
        ...


@runtime_checkable
class DataSourceProtocol(Protocol):
    def iter_events(
        self,
        *,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> Iterable[HistoricalRecord]:
        ...


@runtime_checkable
class ExecutionModelProtocol(Protocol):
    def execute(self, order: SimOrder, market_price: float, timestamp_ns: int) -> SimFill:
        ...


@runtime_checkable
class DepthExecutionModelProtocol(ExecutionModelProtocol, Protocol):
    def execute_depth(
        self,
        order: SimOrder,
        book: OrderBook,
        timestamp_ns: int,
        queue_evidence: Iterable[QueueEvidence] = (),
    ) -> ExecutionResult:
        ...


@runtime_checkable
class AtomicExecutionModelProtocol(DepthExecutionModelProtocol, Protocol):
    def execute_many_atomic(
        self,
        legs: Iterable[tuple[SimOrder, OrderBook, int]],
    ) -> AtomicExecutionResult:
        ...


@runtime_checkable
class PortfolioProtocol(Protocol):
    initial_cash: float

    @property
    def trades(self) -> tuple[object, ...]:
        ...

    def apply_fill(self, fill: SimFill, marks: Mapping[str, float] | None = None) -> object:
        ...

    def snapshot(self, marks: Mapping[str, float] | None = None) -> PortfolioSnapshot:
        ...
