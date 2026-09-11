"""Restart-safe incremental event backtest runner."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from app.backtesting.engine import (
    BacktestEngine,
    BacktestResult,
    EventContext,
    EventStrategy,
    _build_result,
    _build_trade,
    _normalize_event_signal,
)
from app.backtesting.event_state import EventExecutionState
from app.backtesting.historical_catalog import HistoricalRecord


def run_resumable_events(
    engine: BacktestEngine,
    events: Iterable[HistoricalRecord],
    strategy: EventStrategy,
    *,
    ledger,
    run_id: str,
    price_field: str = "price",
    chunk_size: int = 500,
) -> BacktestResult:
    """Run checkpointed event chunks without materializing the full stream.

    The input may be a generator or another one-pass iterable. On restart,
    events through the persisted cursor are replayed into a fresh execution
    state, then the remaining stream is processed in bounded chunks. Only the
    current chunk and trades produced by that chunk are held in memory.
    """
    if not run_id.strip():
        raise ValueError("run_id is required")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not price_field.strip():
        raise ValueError("price_field is required")

    checkpoint = ledger.checkpoint(run_id)
    state = EventExecutionState(
        capital=engine.config.initial_capital,
        peak_capital=engine.config.initial_capital,
    )

    stream: Iterator[HistoricalRecord] = iter(events)
    if checkpoint:
        found = False
        replay_chunk: list[HistoricalRecord] = []
        cursor = checkpoint["cursor"]
        for record in stream:
            replay_chunk.append(record)
            if _cursor(record) == cursor:
                found = True
                _run_chunk(engine, replay_chunk, strategy, state, price_field=price_field)
                break
            if len(replay_chunk) >= chunk_size:
                _run_chunk(engine, replay_chunk, strategy, state, price_field=price_field)
                replay_chunk.clear()
        if not found:
            raise ValueError("persisted checkpoint cursor was not found in event stream")

    processed_any = False
    persisted_trades: list = []
    chunk: list[HistoricalRecord] = []
    for record in stream:
        chunk.append(record)
        if len(chunk) < chunk_size:
            continue
        trades = _run_chunk(engine, chunk, strategy, state, price_field=price_field)
        if trades:
            ledger.append_next(run_id, trades)
            persisted_trades.extend(trades)
        ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))
        processed_any = True
        chunk.clear()

    if chunk:
        trades = _run_chunk(engine, chunk, strategy, state, price_field=price_field)
        if trades:
            ledger.append_next(run_id, trades)
            persisted_trades.extend(trades)
        ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))
        processed_any = True

    if not processed_any:
        return _empty_result(engine, ledger, run_id)

    return _build_result(
        engine.config.initial_capital,
        state.capital,
        persisted_trades,
        state.max_drawdown,
    )


def _run_chunk(
    engine: BacktestEngine,
    events: Iterable[HistoricalRecord],
    strategy: EventStrategy,
    state: EventExecutionState,
    *,
    price_field: str,
) -> tuple:
    trades = []
    for record in events:
        if record.timestamp_ns < 0:
            raise ValueError("event timestamp_ns cannot be negative")
        key = (record.timestamp_ns, record.sequence if record.sequence is not None else -1)
        if state.previous_key is not None and key < state.previous_key:
            raise ValueError("events must be ordered by timestamp_ns and sequence")
        state.previous_key = key
        signal = _normalize_event_signal(
            strategy(
                EventContext(
                    record.timestamp_ns,
                    record.sequence,
                    record.source,
                    record.instrument,
                    record.payload,
                    record,
                )
            )
        )
        if signal.action in {"HOLD", "NONE"}:
            continue
        price = signal.price
        if price is None:
            raw_price = record.payload.get(price_field)
            if not isinstance(raw_price, (int, float)) or isinstance(raw_price, bool):
                raise ValueError(
                    f"event payload must contain numeric {price_field!r} or signal price"
                )
            price = float(raw_price)
        if state.open_trade is None and signal.action == "BUY":
            state.open_trade = (
                record.timestamp_ns,
                price * (1.0 + engine.config.slippage_rate),
            )
        elif state.open_trade is not None and signal.action == "SELL":
            entry_timestamp, entry_price = state.open_trade
            exit_price = price * (1.0 - engine.config.slippage_rate)
            trade = _build_trade(
                engine.config,
                entry_timestamp,
                entry_price,
                record.timestamp_ns,
                exit_price,
            )
            state.capital += trade.net_pnl
            state.peak_capital = max(state.peak_capital, state.capital)
            state.max_drawdown = max(
                state.max_drawdown,
                (state.peak_capital - state.capital) / state.peak_capital,
            )
            trades.append(trade)
            state.open_trade = None
    return tuple(trades)


def _empty_result(engine, ledger, run_id):
    pnl = ledger.net_pnl(run_id)
    initial = engine.config.initial_capital
    return BacktestResult(initial, initial + pnl, pnl, pnl / initial, (), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _cursor(record: HistoricalRecord) -> str:
    return f"{record.timestamp_ns}:{record.sequence if record.sequence is not None else -1}"
