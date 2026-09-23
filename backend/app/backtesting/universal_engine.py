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
from app.backtesting.execution import ExecutionConfig, ExecutionSide, ExecutionSimulator, OrderBook, SimOrder
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot, RiskConfig
from app.backtesting.historical_catalog import HistoricalRecord
from app.backtesting.result_ledger import BacktestFill, EquityPoint as LedgerEquityPoint
from app.backtesting.strategy import restore_strategy_state, strategy_state
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
        trade_reporter: TradeReporterProtocol | None = None,
        retain_history: bool = True,
        checkpoint_store: CheckpointStore | None = None,
        checkpoint_every_events: int | None = None,
        resume: bool = False,
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
        if checkpoint_every_events is not None and (
            isinstance(checkpoint_every_events, bool)
            or not isinstance(checkpoint_every_events, int)
            or checkpoint_every_events <= 0
        ):
            raise ValueError("checkpoint_every_events must be a positive integer or None")
        if resume and result_writer is None:
            raise ValueError("resume requires a result_writer")
        if (checkpoint_store is not None or checkpoint_every_events is not None) and result_writer is None:
            raise ValueError("checkpointing requires a result_writer")
        self.portfolio = portfolio if portfolio is not None else Portfolio(initial_capital, risk_config)
        self.execution = execution if execution is not None else ExecutionSimulator(execution_config)
        self.quantity = quantity
        self.clock = clock if clock is not None else BacktestClock()
        if trade_reporter is not None and not isinstance(trade_reporter, TradeReporterProtocol):
            raise TypeError("trade_reporter must satisfy TradeReporterProtocol")
        self.result_writer = result_writer
        self.trade_reporter = trade_reporter
        self.retain_history = retain_history
        self.checkpoint_store = checkpoint_store if checkpoint_store is not None else (
            result_writer.checkpoints if result_writer is not None else None
        )
        self.checkpoint_every_events = checkpoint_every_events
        self.resume = resume
        self._run_started = False
        self._fill_sequence = 0

    def _begin_run(self) -> None:
        """Enforce one isolated replay lifecycle per engine instance."""
        if self._run_started:
            raise RuntimeError("UniversalEventBacktestEngine instances are single-use; create a new engine for another run")
        self._run_started = True

    @staticmethod
    def _checkpoint_identity(identity) -> dict[str, object]:
        """Serialize canonical event identity without depending on dataclass internals."""
        return {
            "timestamp_ns": identity.timestamp_ns,
            "source": identity.source,
            "instrument": identity.instrument,
            "timeframe": identity.timeframe,
            "sequence": identity.sequence,
        }

    def _build_checkpoint_state(
        self,
        *,
        processed_events: int,
        replay_sequence: int,
        previous_identity,
        last_marks: dict[str, float],
        accumulator: StreamingStatisticsAccumulator,
        peak_equity: float,
        strategy: object,
    ) -> dict[str, object]:
        """Build the complete deterministic state needed to resume after this event."""
        if processed_events <= 0 or previous_identity is None:
            raise ValueError("checkpoint requires at least one processed event")
        return {
            "source_cursor": processed_events,
            "source_event_identity": self._checkpoint_identity(previous_identity),
            "portfolio_state": dict(self.portfolio.export_state()),
            "strategy_state": dict(strategy_state(strategy)),
            "reporter_state": dict(self.trade_reporter.get_state()) if self.trade_reporter is not None and callable(getattr(self.trade_reporter, "get_state", None)) else {},
            "statistics_state": dict(accumulator.export_state()),
            "last_marks": dict(last_marks),
            "replay_sequence": replay_sequence,
            "fill_sequence": self._fill_sequence,
            "peak_equity": peak_equity,
            "clock_now_ns": self.clock.now_ns,
        }


    @staticmethod
    def _restore_checkpoint_state(
        engine,
        checkpoint,
        strategy,
        accumulator: StreamingStatisticsAccumulator,
        last_marks: dict[str, float],
    ) -> tuple[int, int, object, float]:
        state = checkpoint.state
        portfolio_state = state.get("portfolio_state")
        if not isinstance(portfolio_state, dict):
            raise ValueError("checkpoint missing portfolio_state")
        engine.portfolio.restore_state(portfolio_state)
        saved_strategy = state.get("strategy_state", {})
        if not isinstance(saved_strategy, dict):
            raise ValueError("checkpoint strategy_state must be a dictionary")
        restore_strategy_state(strategy, saved_strategy)
        saved_reporter = state.get("reporter_state", {})
        if not isinstance(saved_reporter, dict):
            raise ValueError("checkpoint reporter_state must be a dictionary")
        if saved_reporter:
            setter = getattr(engine.trade_reporter, "set_state", None)
            if engine.trade_reporter is None or not callable(setter):
                raise ValueError("checkpoint contains reporter_state but reporter cannot restore it")
            setter(dict(saved_reporter))
        saved_stats = state.get("statistics_state")
        if not isinstance(saved_stats, dict):
            raise ValueError("checkpoint missing statistics_state")
        accumulator.restore_state(saved_stats)
        saved_marks = state.get("last_marks", {})
        if not isinstance(saved_marks, dict):
            raise ValueError("checkpoint last_marks must be a dictionary")
        last_marks.clear()
        last_marks.update({str(k): float(v) for k, v in saved_marks.items()})
        replay_sequence = state.get("replay_sequence")
        fill_sequence = state.get("fill_sequence")
        peak_equity = state.get("peak_equity")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (replay_sequence, fill_sequence)):
            raise ValueError("checkpoint sequence state is invalid")
        if isinstance(peak_equity, bool) or not isinstance(peak_equity, (int, float)):
            raise ValueError("checkpoint peak_equity is invalid")
        engine._fill_sequence = fill_sequence
        return int(state["source_cursor"]), int(replay_sequence), state["source_event_identity"], float(peak_equity)

    def _load_checkpoint_for_resume(
        self,
        *,
        strategy: object,
        accumulator: StreamingStatisticsAccumulator,
        last_marks: dict[str, float],
    ) -> tuple[int, int, object | None, float]:
        if not self.resume:
            return 0, 0, None, self.portfolio.initial_cash
        if self.checkpoint_store is None or self.result_writer is None:
            raise ValueError("resume requires checkpointing and a result writer")
        checkpoint = self.checkpoint_store.load(self.result_writer.spec.run_id)
        if checkpoint is None:
            raise ValueError("no checkpoint available for resume")
        cursor, replay_sequence, saved_identity, peak_equity = self._restore_checkpoint_state(
            self, checkpoint, strategy, accumulator, last_marks
        )
        if cursor != checkpoint.processed_events:
            raise ValueError("checkpoint source_cursor does not match processed_events")
        clock_now_ns = checkpoint.state.get("clock_now_ns")
        if isinstance(clock_now_ns, bool) or not isinstance(clock_now_ns, int) or clock_now_ns < 0:
            raise ValueError("checkpoint clock_now_ns is invalid")
        self.clock.advance_to(clock_now_ns)
        return cursor, replay_sequence, saved_identity, peak_equity

    @staticmethod
    def _verify_checkpoint_identity(record: HistoricalRecord, saved_identity: object) -> None:
        if not isinstance(saved_identity, dict):
            raise ValueError("checkpoint missing source_event_identity")
        actual = event_identity(record)
        expected = (
            saved_identity.get("timestamp_ns"),
            saved_identity.get("source"),
            saved_identity.get("instrument"),
            saved_identity.get("timeframe"),
            saved_identity.get("sequence"),
        )
        if (actual.timestamp_ns, actual.source, actual.instrument, actual.timeframe, actual.sequence) != expected:
            raise ValueError("checkpoint source_event_identity does not match source")

    def _save_checkpoint_if_due(
        self,
        *,
        processed_events: int,
        replay_sequence: int,
        previous_identity,
        last_marks: dict[str, float],
        accumulator: StreamingStatisticsAccumulator,
        peak_equity: float,
        strategy: object,
        record: HistoricalRecord,
    ) -> None:
        if self.checkpoint_store is None or self.checkpoint_every_events is None:
            return
        if processed_events % self.checkpoint_every_events != 0:
            return
        snapshot = self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({})
        self.checkpoint_store.save(
            ReplayCheckpoint(
                run_id=self.result_writer.spec.run_id,
                timestamp_ns=record.timestamp_ns,
                sequence=replay_sequence,
                processed_events=processed_events,
                realized_pnl=snapshot.realized_pnl,
                state=self._build_checkpoint_state(
                    processed_events=processed_events,
                    replay_sequence=replay_sequence,
                    previous_identity=previous_identity,
                    last_marks=last_marks,
                    accumulator=accumulator,
                    peak_equity=peak_equity,
                    strategy=strategy,
                ),
                instrument=record.instrument,
                event_type="REPLAY_EVENT",
            )
        )

    def _record_replay_point(
        self,
        sequence: int,
        record: HistoricalRecord,
        snapshot: PortfolioSnapshot,
        accumulator: StreamingStatisticsAccumulator,
        peak_equity: float,
    ) -> tuple[float, EquityPoint]:
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
        return peak_equity, point

    def run_source(self, source: DataSourceProtocol, strategy: StrategyProtocol, *, start_ns: int | None = None, end_ns: int | None = None, price_field: str = "price", order_book_field: str | None = None) -> UniversalBacktestResult:
        """Run directly from a streaming DataSource without materializing its events."""
        return self.run(source.iter_events(start_ns=start_ns, end_ns=end_ns), strategy, price_field=price_field, order_book_field=order_book_field)

    def run_context(self, context, *, price_field: str = "price", order_book_field: str | None = None) -> UniversalBacktestResult:
        """Run the single-leg strategy and data source bound to one immutable RunContext."""
        if context.execution is not self.execution or context.portfolio is not self.portfolio or context.clock is not self.clock:
            raise ValueError("RunContext dependencies do not match this engine")
        if context.result_writer is not self.result_writer:
            raise ValueError("RunContext result_writer does not match this engine")
        return self.run_source(
            context.data_source,
            context.strategy,
            start_ns=context.spec.start_ns,
            end_ns=context.spec.end_ns,
            price_field=price_field,
            order_book_field=order_book_field,
        )

    def run_multi_leg_context(self, context) -> UniversalBacktestResult:
        """Run the multi-leg strategy and data source bound to one immutable RunContext."""
        if context.execution is not self.execution or context.portfolio is not self.portfolio or context.clock is not self.clock:
            raise ValueError("RunContext dependencies do not match this engine")
        if context.result_writer is not self.result_writer:
            raise ValueError("RunContext result_writer does not match this engine")
        return self.run_multi_leg(
            context.data_source.iter_events(
                start_ns=context.spec.start_ns,
                end_ns=context.spec.end_ns,
            ),
            context.strategy,
        )

    def _run_with_writer_lifecycle(self, run_callable) -> UniversalBacktestResult:
        """Complete or fail a durable run while preserving the original exception."""
        try:
            result = run_callable()
        except Exception as exc:
            if self.result_writer is not None:
                self.result_writer.fail(str(exc))
            raise
        if self.result_writer is not None:
            self.result_writer.complete()
        return result

    def run_multi_leg(self, events: Iterable[HistoricalRecord], strategy: MultiLegStrategyProtocol) -> UniversalBacktestResult:
        """Run a strategy that returns a complete atomic depth-execution basket per event."""
        self._begin_run()
        return self._run_with_writer_lifecycle(lambda: self._run_multi_leg(events, strategy))

    def _run_multi_leg(self, events: Iterable[HistoricalRecord], strategy: MultiLegStrategyProtocol) -> UniversalBacktestResult:
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
        resume_cursor, replay_sequence, saved_identity, peak_equity = self._load_checkpoint_for_resume(
            strategy=strategy, accumulator=accumulator, last_marks=last_marks
        )
        processed_events = 0

        for record in events:
            checkpoint_due = (
                self.result_writer is not None
                and self.checkpoint_every_events is not None
                and (processed_events + 1) % self.checkpoint_every_events == 0
            )
            with (
                self.result_writer.transaction()
                if checkpoint_due
                else nullcontext()
            ):
                if self.resume and processed_events < resume_cursor:
                    processed_events += 1
                    previous_identity = event_identity(record)
                    previous_key = event_order_key(record)
                    if processed_events == resume_cursor:
                        self._verify_checkpoint_identity(record, saved_identity)
                    continue
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
                        if isinstance(strategy, AtomicExecutionAwareProtocol):
                            strategy.on_atomic_execution(result)
                        raise ValueError(result.reason or "atomic multi-leg execution rejected")
                    trade_start = len(self.portfolio.trades)
                    snapshot = self.portfolio.apply_fills_atomic(result.fills, last_marks)
                    accounting_trades = tuple(self.portfolio.trades[trade_start:])
                    if self.result_writer is not None:
                        durable_fills = []
                        fill_offset = 0
                        for leg_result in result.leg_results:
                            if len(leg_result.reference_prices) != len(leg_result.fills):
                                raise ValueError("execution result reference_prices must align with fills")
                            for fill, reference_price in zip(leg_result.fills, leg_result.reference_prices):
                                durable_fills.append(BacktestFill(
                                    fill_id=f"{replay_sequence}:{self._fill_sequence}:{fill.order_id}",
                                    order_id=fill.order_id,
                                    sequence=self._fill_sequence,
                                    timestamp_ns=fill.filled_at_ns,
                                    instrument=fill.instrument,
                                    side=fill.side.value,
                                    quantity=fill.quantity,
                                    price=fill.price,
                                    fee=fill.fee,
                                    metadata={"reference_price": reference_price},
                                ))
                                self._fill_sequence += 1
                                fill_offset += 1
                        if fill_offset != len(result.fills):
                            raise ValueError("execution result leg fills do not match atomic fills")
                        self.result_writer.record_fills(tuple(durable_fills))
                    if isinstance(strategy, AtomicExecutionAwareProtocol):
                        strategy.on_atomic_execution(result)
                    if self.trade_reporter is not None:
                        self.trade_reporter.record_atomic_trade(
                            AtomicTradeReportInput(result, accounting_trades)
                        )

                peak_equity, point = self._record_replay_point(replay_sequence, record, snapshot, accumulator, peak_equity)
                replay_sequence += 1
                processed_events += 1
                if checkpoint_due:
                    self._save_checkpoint_if_due(
                            processed_events=processed_events,
                            replay_sequence=replay_sequence,
                            previous_identity=previous_identity,
                            last_marks=last_marks,
                            accumulator=accumulator,
                            peak_equity=peak_equity,
                            strategy=strategy,
                            record=record,
                        )
                if self.retain_history:
                    snapshots.append(snapshot)
                    equity_curve.append(point)

        if self.resume and resume_cursor > processed_events:
            raise ValueError("checkpoint source_cursor exceeds available source events")
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
        self._begin_run()
        return self._run_with_writer_lifecycle(lambda: self._run(events, strategy, price_field=price_field, order_book_field=order_book_field))

    def _run(self, events: Iterable[HistoricalRecord], strategy: StrategyProtocol, *, price_field: str = "price", order_book_field: str | None = None) -> UniversalBacktestResult:
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
        resume_cursor, replay_sequence, saved_identity, peak_equity = self._load_checkpoint_for_resume(
            strategy=strategy, accumulator=accumulator, last_marks=last_marks
        )
        processed_events = 0

        for record in events:
            checkpoint_due = (
                self.result_writer is not None
                and self.checkpoint_every_events is not None
                and (processed_events + 1) % self.checkpoint_every_events == 0
            )
            with (
                self.result_writer.transaction()
                if checkpoint_due
                else nullcontext()
            ):
                if self.resume and processed_events < resume_cursor:
                    processed_events += 1
                    previous_identity = event_identity(record)
                    previous_key = event_order_key(record)
                    if processed_events == resume_cursor:
                        self._verify_checkpoint_identity(record, saved_identity)
                    continue
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
                        self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({}),
                        (),
                        (self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({})).available_margin,
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
                            if self.result_writer is not None:
                                durable_fills = []
                                if len(result.reference_prices) != len(result.fills):
                                    raise ValueError("execution result reference_prices must align with fills")
                                for fill, reference_price in zip(result.fills, result.reference_prices):
                                    durable_fills.append(BacktestFill(
                                        fill_id=f"{replay_sequence}:{self._fill_sequence}:{fill.order_id}",
                                        order_id=fill.order_id,
                                        sequence=self._fill_sequence,
                                        timestamp_ns=fill.filled_at_ns,
                                        instrument=fill.instrument,
                                        side=fill.side.value,
                                        quantity=fill.quantity,
                                        price=fill.price,
                                        fee=fill.fee,
                                        metadata={"reference_price": reference_price},
                                    ))
                                    self._fill_sequence += 1
                                self.result_writer.record_fills(tuple(durable_fills))
                    else:
                        fill = self.execution.execute(order, float(price), record.timestamp_ns)
                        self.portfolio.apply_fill(fill, last_marks)
                        if self.result_writer is not None:
                            self.result_writer.record_fills((BacktestFill(
                                fill_id=f"{replay_sequence}:{self._fill_sequence}:{fill.order_id}",
                                order_id=fill.order_id,
                                sequence=self._fill_sequence,
                                timestamp_ns=fill.filled_at_ns,
                                instrument=fill.instrument,
                                side=fill.side.value,
                                quantity=fill.quantity,
                                price=fill.price,
                                fee=fill.fee,
                            ),))
                            self._fill_sequence += 1
    
                snapshot = self.portfolio.snapshot(last_marks) if last_marks else self.portfolio.snapshot({})
                peak_equity, point = self._record_replay_point(replay_sequence, record, snapshot, accumulator, peak_equity)
                replay_sequence += 1
                processed_events += 1
                if checkpoint_due:
                    self._save_checkpoint_if_due(
                        processed_events=processed_events,
                        replay_sequence=replay_sequence,
                        previous_identity=previous_identity,
                        last_marks=last_marks,
                        accumulator=accumulator,
                        peak_equity=peak_equity,
                        strategy=strategy,
                        record=record,
                    )
                if self.retain_history:
                    snapshots.append(snapshot)
                    equity_curve.append(point)
    
        if self.resume and resume_cursor > processed_events:
            raise ValueError("checkpoint source_cursor exceeds available source events")
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