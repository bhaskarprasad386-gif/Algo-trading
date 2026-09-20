"""Portfolio-backed universal event backtesting path.

This module is intentionally separate from the legacy single-position BacktestEngine
API.  It uses the existing Portfolio and ExecutionSimulator primitives so event
backtests can hold independent positions per instrument and can represent both
long and short exposure without duplicating accounting logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.backtesting.engine import EventContext, EventSignal, EventStrategy, _normalize_event_signal
from app.backtesting.contracts import AtomicExecutionModelProtocol, DataSourceProtocol, DepthExecutionModelProtocol, ExecutionModelProtocol, MultiLegStrategyProtocol, PortfolioProtocol, StrategyProtocol
from app.backtesting.clock import BacktestClock, ClockProtocol
from app.backtesting.event_model import event_identity, event_order_key
from app.backtesting.execution import ExecutionConfig, ExecutionSide, ExecutionSimulator, OrderBook, SimOrder
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot, RiskConfig
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.result_ledger import EquityPoint as LedgerEquityPoint
from app.backtesting.statistics import BacktestStatistics, EquityPoint, StreamingStatisticsAccumulator


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
    max_drawdown: float
    cagr: float | None
    snapshots: tuple[PortfolioSnapshot, ...]
    equity_curve: tuple[EquityPoint, ...]
    fill_count: int


class UniversalEventBacktestEngine:
    """Run event strategies against a multi-instrument portfolio.

    BUY and SELL are portfolio orders rather than implicit round-trip markers:
    BUY can increase a long or reduce a short, while SELL can increase a short
    or reduce a long.  The instrument comes from EventContext, so positions are
    isolated by instrument and simultaneous positions are supported.
    """

    def __init__(
        self,
        initial_capital: float,
        *,
        risk_config: RiskConfig | None = None,
        execution_config: ExecutionConfig | None = None,
        quantity: int = 1,
        portfolio: PortfolioProtocol | None = None,
        execution: ExecutionModelProtocol | None = None,
        clock: ClockProtocol | None = None,
        result_writer=None,
        retain_history: bool = True,
    ) -> None:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if portfolio is not None and (risk_config is not None or initial_capital != portfolio.initial_cash):
            raise ValueError("initial_capital/risk_config cannot be combined with a custom portfolio")
        if execution is not None and execution_config is not None:
            raise ValueError("execution_config cannot be combined with a custom execution model")
        if not isinstance(retain_history, bool):
            raise ValueError("retain_history must be a boolean")
        if not retain_history and result_writer is None:
            raise ValueError("retain_history=False requires a result_writer")
        self.portfolio = portfolio if portfolio is not None else Portfolio(initial_capital, risk_config)
        self.execution = execution if execution is not None else ExecutionSimulator(execution_config)
        self.quantity = quantity
        self.clock = clock if clock is not None else BacktestClock()
        self.result_writer = result_writer
        self.retain_history = retain_history

    def _record_replay_point(
        self,
        sequence: int,
        record: HistoricalRecord,
        snapshot: PortfolioSnapshot,
        accumulator: StreamingStatisticsAccumulator,
        peak_equity: float,
    ) -> float:
        point = EquityPoint(
            record.timestamp_ns,
            snapshot.equity,
            snapshot.realized_pnl,
            snapshot.unrealized_pnl,
        )
        accumulator.update(point)
        peak_equity = max(peak_equity, snapshot.equity)
        if self.result_writer is not None:
            self.result_writer.record_event(
                sequence,
                record.timestamp_ns,
                "REPLAY_EVENT",
                {
                    "source": record.source,
                    "instrument": record.instrument,
                    "timestamp_ns": record.timestamp_ns,
                    "source_sequence": record.sequence,
                },
            )
            self.result_writer.record_equity(
                LedgerEquityPoint(
                    record.timestamp_ns,
                    snapshot.equity,
                    snapshot.realized_pnl,
                    snapshot.unrealized_pnl,
                    (peak_equity - snapshot.equity) / peak_equity
                    if peak_equity > 0
                    else 0.0,
                )
            )
        return peak_equity

    def run_source(self, source: DataSourceProtocol, strategy: StrategyProtocol, *, start_ns: int | None = None, end_ns: int | None = None, price_field: str = "price", order_book_field: str | None = None) -> UniversalBacktestResult:
        """Run directly from a streaming DataSource without materializing its events."""
        return self.run(source.iter_events(start_ns=start_ns, end_ns=end_ns), strategy, price_field=price_field, order_book_field=order_book_field)

    def run_multi_leg(self, events: Iterable[HistoricalRecord], strategy: MultiLegStrategyProtocol) -> UniversalBacktestResult:
        """Run a strategy that returns a complete atomic depth-execution basket per event."""
        if not isinstance(self.execution, AtomicExecutionModelProtocol):
            raise TypeError("multi-leg execution requires an atomic execution model")
        if not hasattr(self.portfolio, "apply_fills_atomic"):
            raise TypeError("multi-leg execution requires atomic portfolio accounting")

        previous_key: tuple[int, str, str, str, int] | None = None
        previous_identity = None
        snapshots: list[PortfolioSnapshot] = []
        equity_curve: list[EquityPoint] = []
        last_marks: dict[str, float] = {}
        accumulator = StreamingStatisticsAccumulator(self.portfolio.initial_cash)
        peak_equity = self.portfolio.initial_cash
        replay_sequence = 0

        for record in events:
            if not isinstance(record.timestamp_ns, int) or isinstance(record.timestamp_ns, bool) or record.timestamp_ns < 0:
                raise ValueError("event timestamp_ns must be a non-negative integer")
            if not isinstance(record.instrument, str) or not record.instrument.strip():
                raise ValueError("event instrument is required")
            if not isinstance(record.source, str) or not record.source.strip():
                raise ValueError("event source is required")
            if record.sequence is not None and (not isinstance(record.sequence, int) or isinstance(record.sequence, bool) or record.sequence < 0):
                raise ValueError("event sequence must be a non-negative integer or None")
            key = event_order_key(record)
            identity = event_identity(record)
            if previous_identity is not None and identity == previous_identity:
                raise ValueError("duplicate event identity")
            if previous_key is not None and key <= previous_key:
                raise ValueError("events must be strictly ordered by deterministic event order")
            previous_identity = identity
            previous_key = key
            self.clock.advance_to(record.timestamp_ns)

            legs = strategy(EventContext(record.timestamp_ns, record.sequence, record.source, record.instrument, record.payload, record))
            if legs is None:
                legs = ()
            legs = tuple(legs)
            if not legs:
                snapshot = self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({})
            else:
                for order, book, timestamp_ns in legs:
                    if timestamp_ns != record.timestamp_ns:
                        raise ValueError("multi-leg order timestamps must match the dispatch event")
                    levels = book.asks if order.side == ExecutionSide.BUY else book.bids
                    if not levels:
                        raise ValueError(f"missing executable depth for {order.instrument!r}")
                    last_marks[order.instrument] = float(levels[0].price)
                result = self.execution.execute_many_atomic(legs)
                if result.rejected:
                    raise ValueError(result.reason or "atomic multi-leg execution rejected")
                snapshot = self.portfolio.apply_fills_atomic(result.fills, last_marks)

            peak_equity = self._record_replay_point(replay_sequence, record, snapshot, accumulator, peak_equity)
            replay_sequence += 1
            if self.retain_history:
                snapshots.append(snapshot)
                equity_curve.append(EquityPoint(record.timestamp_ns, snapshot.equity, snapshot.realized_pnl, snapshot.unrealized_pnl))

        final_snapshot = self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({})
        stats: BacktestStatistics = accumulator.finalize()
        return UniversalBacktestResult(
            initial_capital=self.portfolio.initial_cash,
            final_equity=final_snapshot.equity,
            realized_pnl=final_snapshot.realized_pnl,
            unrealized_pnl=final_snapshot.unrealized_pnl,
            net_pnl=stats.net_pnl,
            total_return=stats.total_return,
            sharpe_ratio=stats.sharpe_ratio,
            sortino_ratio=stats.sortino_ratio,
            max_drawdown=stats.max_drawdown,
            cagr=stats.cagr,
            snapshots=tuple(snapshots),
            equity_curve=tuple(equity_curve),
            fill_count=len(self.portfolio.trades),
        )

    def run(self, events: Iterable[HistoricalRecord], strategy: StrategyProtocol, *, price_field: str = "price", order_book_field: str | None = None) -> UniversalBacktestResult:
        if not isinstance(price_field, str) or not price_field.strip():
            raise ValueError("price_field is required")

        previous_key: tuple[int, str, str, str, int] | None = None
        previous_identity = None
        snapshots: list[PortfolioSnapshot] = []
        equity_curve: list[EquityPoint] = []
        last_marks: dict[str, float] = {}
        accumulator = StreamingStatisticsAccumulator(self.portfolio.initial_cash)
        peak_equity = self.portfolio.initial_cash
        replay_sequence = 0

        for record in events:
            if not isinstance(record.timestamp_ns, int) or isinstance(record.timestamp_ns, bool) or record.timestamp_ns < 0:
                raise ValueError("event timestamp_ns must be a non-negative integer")
            if not isinstance(record.instrument, str) or not record.instrument.strip():
                raise ValueError("event instrument is required")
            if not isinstance(record.source, str) or not record.source.strip():
                raise ValueError("event source is required")
            if record.sequence is not None and (not isinstance(record.sequence, int) or isinstance(record.sequence, bool) or record.sequence < 0):
                raise ValueError("event sequence must be a non-negative integer or None")
            key = event_order_key(record)
            identity = event_identity(record)
            if previous_identity is not None and identity == previous_identity:
                raise ValueError("duplicate event identity")
            if previous_key is not None and key <= previous_key:
                raise ValueError("events must be strictly ordered by deterministic event order")
            previous_identity = identity
            previous_key = key
            self.clock.advance_to(record.timestamp_ns)

            raw_price = record.payload.get(price_field)
            if raw_price is not None:
                if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
                    raise ValueError(f"event payload {price_field!r} must be numeric")
                if raw_price <= 0:
                    raise ValueError(f"event payload {price_field!r} must be positive")
                last_marks[record.instrument] = float(raw_price)

            signal = _normalize_event_signal(
                strategy(EventContext(
                    record.timestamp_ns,
                    record.sequence,
                    record.source,
                    record.instrument,
                    record.payload,
                    record,
                ))
            )
            if signal.action not in {"HOLD", "NONE"}:
                price = signal.price
                if price is None:
                    price = raw_price
                if price is None or isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
                    raise ValueError(f"event payload must contain numeric positive {price_field!r} or signal price")
                last_marks[record.instrument] = float(price)

                side = ExecutionSide.BUY if signal.action == "BUY" else ExecutionSide.SELL
                order = SimOrder(
                    order_id=f"event-{record.timestamp_ns}-{record.sequence if record.sequence is not None else 'na'}-{record.instrument}",
                    instrument=record.instrument,
                    side=side,
                    quantity=self.quantity,
                    submitted_at_ns=record.timestamp_ns,
                )
                if order_book_field is not None:
                    book = record.payload.get(order_book_field)
                    if not isinstance(self.execution, DepthExecutionModelProtocol):
                        raise TypeError("order_book execution requires a depth execution model")
                    if not isinstance(book, OrderBook):
                        raise TypeError(f"event payload {order_book_field!r} must contain an OrderBook")
                    result = self.execution.execute_depth(order, book, record.timestamp_ns)
                    if result.rejected:
                        raise ValueError(result.reason or "depth execution rejected")
                    if result.fills:
                        self.portfolio.apply_fills_atomic(result.fills, last_marks)
                else:
                    fill = self.execution.execute(order, float(price), record.timestamp_ns)
                    self.portfolio.apply_fill(fill, last_marks)

            snapshot = self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({})
            peak_equity = self._record_replay_point(replay_sequence, record, snapshot, accumulator, peak_equity)
            replay_sequence += 1
            if self.retain_history:
                snapshots.append(snapshot)
                equity_curve.append(EquityPoint(record.timestamp_ns, snapshot.equity, snapshot.realized_pnl, snapshot.unrealized_pnl))

        final_snapshot = self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({})
        stats: BacktestStatistics = accumulator.finalize()
        return UniversalBacktestResult(
            initial_capital=self.portfolio.initial_cash,
            final_equity=final_snapshot.equity,
            realized_pnl=final_snapshot.realized_pnl,
            unrealized_pnl=final_snapshot.unrealized_pnl,
            net_pnl=stats.net_pnl,
            total_return=stats.total_return,
            sharpe_ratio=stats.sharpe_ratio,
            sortino_ratio=stats.sortino_ratio,
            max_drawdown=stats.max_drawdown,
            cagr=stats.cagr,
            snapshots=tuple(snapshots),
            equity_curve=tuple(equity_curve),
            fill_count=len(self.portfolio.trades),
        )