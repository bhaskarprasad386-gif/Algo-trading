"""Stable contracts for the backtesting core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

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
from app.backtesting.execution import ExecutionSide


@runtime_checkable
class StrategyProtocol(Protocol):
    def __call__(self, context: EventContext) -> EventSignal | str | None:
        ...


@runtime_checkable
class MultiLegStrategyProtocol(Protocol):
    def __call__(self, context: EventContext) -> Iterable[tuple[SimOrder, OrderBook, int]] | None:
        ...


@runtime_checkable
class AtomicExecutionAwareProtocol(Protocol):
    """Optional strategy callback for committing multi-leg execution state."""

    def on_atomic_execution(self, result: AtomicExecutionResult) -> None:
        ...


class DataSourceProtocol(Protocol):
    def iter_events(self, *, start_ns: int | None = None, end_ns: int | None = None) -> Iterable[HistoricalRecord]:
        ...


@runtime_checkable
class ExecutionModelProtocol(Protocol):
    def execute(self, order: SimOrder, market_price: float, timestamp_ns: int) -> SimFill:
        ...


@runtime_checkable
class DepthExecutionModelProtocol(ExecutionModelProtocol, Protocol):
    def execute_depth(self, order: SimOrder, book: OrderBook, timestamp_ns: int, queue_evidence: Iterable[QueueEvidence] = ()) -> ExecutionResult:
        ...


@runtime_checkable
class AtomicExecutionModelProtocol(DepthExecutionModelProtocol, Protocol):
    def execute_many_atomic(self, legs: Iterable[tuple[SimOrder, OrderBook, int]]) -> AtomicExecutionResult:
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


@runtime_checkable
class AccountingProtocol(PortfolioProtocol, Protocol):
    """Accounting boundary for position, P&L, margin and durable state."""

    def apply_fills_atomic(self, fills: Iterable[SimFill], marks: Mapping[str, float] | None = None) -> PortfolioSnapshot:
        ...

    def validate_mark_to_market(self, marks: Mapping[str, float] | None = None) -> PortfolioSnapshot:
        ...

    def reserve_margin(self, order_id: str, amount: float, marks: Mapping[str, float] | None = None) -> float:
        ...

    def release_margin(self, order_id: str, amount: float | None = None) -> float:
        ...

    def export_state(self) -> Mapping[str, Any]:
        ...

    def restore_state(self, state: Mapping[str, Any]) -> None:
        ...


@runtime_checkable
class RunContextProtocol(Protocol):
    """Immutable dependency boundary for one isolated backtest run."""

    spec: Any
    clock: Any
    data_source: Any
    strategy: Any
    execution: ExecutionModelProtocol
    portfolio: PortfolioProtocol
    result_writer: Any


@dataclass(frozen=True)
class RunContext:
    """Concrete immutable context binding all dependencies to one run."""

    spec: Any
    clock: Any
    data_source: Any
    strategy: Any
    execution: ExecutionModelProtocol
    portfolio: PortfolioProtocol
    result_writer: Any = None
    seed: int | None = None
