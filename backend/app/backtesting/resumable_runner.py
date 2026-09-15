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
    """Run checkpointed event chunks without materializing the full stream."""
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
        persisted_count = ledger.count(run_id)
        if persisted_count != int(checkpoint["trade_count"]):
            raise ValueError("checkpoint trade_count does not match durable ledger trade count")
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
    chunk: list[HistoricalRecord] = []
    for record in stream:
        chunk.append(record)
        if len(chunk) < chunk_size:
            continue
        trades = _run_chunk(engine, chunk, strategy, state, price_field=price_field)
        if trades:
            ledger.append_next(run_id, trades)
        ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))
        processed_any = True
        chunk.clear()

    if chunk:
        trades = _run_chunk(engine, chunk, strategy, state, price_field=price_field)
        if trades:
            ledger.append_next(run_id, trades)
        ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))
        processed_any = True

    # A restart may legitimately consume no new records after replaying the
    # checkpoint. Return the reconstructed state, not a synthetic empty result.
    trades = ledger.trades(run_id)
    return _result_from_state(engine, state, trades) if not processed_any else _result_from_state(engine, state, trades)


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
        if state.previous_key is not None and key <= state.previous_key:
            raise ValueError("events must be strictly ordered by timestamp_ns and sequence; duplicate event identity is not allowed")
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
        raw_price = record.payload.get(price_field)
        if raw_price is not None:
            if not isinstance(raw_price, (int, float)) or isinstance(raw_price, bool):
                raise ValueError(f"event payload field {price_field!r} must be numeric when supplied")
            mark_price = float(raw_price)
            if not __import__("math").isfinite(mark_price) or mark_price <= 0:
                raise ValueError(f"event payload field {price_field!r} must be finite and positive")
            state.last_price = mark_price
            state.last_timestamp = record.timestamp_ns

        if signal.action in {"HOLD", "NONE"}:
            _mark_open_trade(engine, state)
            continue
        price = signal.price
        if price is None:
            price = state.last_price
        if price is None:
            raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
        if not __import__("math").isfinite(float(price)) or float(price) <= 0:
            raise ValueError("event execution price must be finite and positive")
        state.last_price = float(price)
        state.last_timestamp = record.timestamp_ns
        if state.open_trade is None and signal.action == "BUY":
            state.open_trade = (
                record.timestamp_ns,
                float(price) * (1.0 + engine.config.slippage_rate),
            )
        elif state.open_trade is not None and signal.action == "SELL":
            entry_timestamp, entry_price = state.open_trade
            exit_price = float(price) * (1.0 - engine.config.slippage_rate)
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
        _mark_open_trade(engine, state)
    return tuple(trades)


def _mark_open_trade(engine: BacktestEngine, state: EventExecutionState) -> None:
    if state.open_trade is None or state.last_price is None:
        return
    marked_capital = state.capital + (
        state.last_price * (1.0 - engine.config.slippage_rate) - state.open_trade[1]
    ) * engine.config.quantity
    state.peak_capital = max(state.peak_capital, state.capital)
    if marked_capital < state.peak_capital:
        state.max_drawdown = max(
            state.max_drawdown,
            (state.peak_capital - marked_capital) / state.peak_capital,
        )


def _result_from_state(engine: BacktestEngine, state: EventExecutionState, trades) -> BacktestResult:
    if state.open_trade is not None and state.last_price is not None:
        unrealized = (
            state.last_price * (1.0 - engine.config.slippage_rate) - state.open_trade[1]
        ) * engine.config.quantity
    else:
        unrealized = 0.0
    final_capital = state.capital + unrealized
    return _build_result(
        engine.config.initial_capital,
        final_capital,
        tuple(trades),
        state.max_drawdown,
        unrealized,
        state.open_trade is not None,
    )


def _cursor(record: HistoricalRecord) -> str:
    return f"{record.timestamp_ns}:{record.sequence if record.sequence is not None else -1}"
