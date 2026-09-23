"""Portfolio-backed universal event backtesting path.

This module is intentionally separate from the legacy single-position BacktestEngine
API.  It uses the existing Portfolio and ExecutionSimulator primitives so event
backtests can hold independent positions per instrument and can represent both
long and short exposure without duplicating accounting logic.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Iterable

from app.backtesting.engine import EventContext, EventSignal, EventStrategy, _normalize_event_signal
from app.backtesting.contracts import AtomicExecutionAwareProtocol, AtomicExecutionModelProtocol, AtomicTradeReportInput, DataSourceProtocol, DepthExecutionModelProtocol, ExecutionModelProtocol, MultiLegStrategyProtocol, PortfolioProtocol, StrategyProtocol, TradeReporterProtocol
from app.backtesting.clock import BacktestClock, ClockProtocol
from app.backtesting.event_model import event_identity, event_order_key
from app.backtesting.checkpoint import CheckpointStore, ReplayCheckpoint
from app.backtesting.execution import ExecutionConfig, ExecutionResult, ExecutionSide, ExecutionSimulator, OrderBook, SimOrder
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot, RiskConfig
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.result_ledger import BacktestFill, EquityPoint as LedgerEquityPoint
from app.backtesting.strategy import restore_strategy_state, strategy_state
from app.backtesting.statistics import BacktestStatistics, EquityPoint, StreamingStatisticsAccumulator
from app.backtesting.universal_order_registry import UniversalOrderRegistry


@dataclass(frozen=True)
class UniversalBacktestResult:
    """Accounting result with a durable marked-equity time series."""

    initial_capital: float
    final_equity: float
    realized_pnl: float
    unrealized_pnl: float
    net_pnl: float
    total_return: float
    sharpe_ratio: float | None
    sortino_ratio: float | None