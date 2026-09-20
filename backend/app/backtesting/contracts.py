"""Stable contracts for the backtesting core.

Concrete implementations remain free to evolve behind these small interfaces.
The contracts deliberately depend on domain values rather than concrete engines,
so the universal engine can later be reused by historical, paper, and live paths.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Protocol, runtime_checkable

from app.backtesting.engine import EventContext, EventSignal
from app.backtesting.execution import SimFill, SimOrder
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
        """Return an ordered event stream without requiring full materialization."""
        ...


@runtime_checkable
class ExecutionModelProtocol(Protocol):
    def execute(self, order: SimOrder, market_price: float, timestamp_ns: int) -> SimFill:
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
