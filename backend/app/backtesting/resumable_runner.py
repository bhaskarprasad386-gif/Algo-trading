"""Restart-safe incremental event backtest runner."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from math import isfinite

from app.backtesting.engine import (
    BacktestEngine,
    BacktestResult,
    EventContext,
    EventStrategy,
    _build_result,
    _build_trade,
    _calculate_liquidation_pnl,
    _ensure_cash_available,
    _execution_price,
    _net_exit_cashflow,
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
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id is required")
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if not isinstance(price_field, str) or not price_field.strip():
        raise ValueError("price_field is required")

    checkpoint = ledger.checkpoint(run_id)
    if checkpoint is not None:
        stored_count = checkpoint.get("trade_count")
        if type(stored_count) is not int or stored_count < 0:
            raise ValueError("persisted checkpoint trade_count is invalid")
        actual_count = ledger.count(run_id)
        if actual_count != stored_count:
            raise ValueError("persisted checkpoint trade_count does not match ledger")

    state = EventExecutionState(capital=engine.config.initial_capital, peak_capital=engine.config.initial_capital)
    stream: Iterator[HistoricalRecord] = iter(events)
    resumed = checkpoint is not None

    if checkpoint:
        found = False
        replay_chunk: list[HistoricalRecord] = []
        cursor = checkpoint["cursor"]
        for record in stream:
            replay_chunk.append(record)
            if _cursor(record) == cursor:
                _run_chunk(engine, replay_chunk, strategy, state, price_field=price_field)
                found = True
                break
            if _legacy_cursor(record) == cursor and _cursor(record) != cursor:
                raise ValueError("legacy checkpoint cursor is ambiguous; restart from a full checkpoint")
            if len(replay_chunk) >= chunk_size:
                _run_chunk(engine, replay_chunk, strategy, state, price_field=price_field)
                replay_chunk.clear()
        if not found:
            raise ValueError("persisted checkpoint cursor was not found in event stream")

    ledger_has_trades = hasattr(ledger, "trades")
    persisted_trades: list = []
    processed_any = resumed
    chunk: list[HistoricalRecord] = []
    for record in stream:
        chunk.append(record)
        if len(chunk) < chunk_size:
            continue
        trades = _run_chunk(engine, chunk, strategy, state, price_field=price_field)
        with ledger.transaction():
            if trades:
                ledger.append_next(run_id, trades)
                if not ledger_has_trades:
                    persisted_trades.extend(trades)
            ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))
        processed_any = True
        chunk.clear()

    if chunk:
        trades = _run_chunk(engine, chunk, strategy, state, price_field=price_field)
        with ledger.transaction():
            if trades:
                ledger.append_next(run_id, trades)
                if not ledger_has_trades:
                    persisted_trades.extend(trades)
            ledger.save_checkpoint(run_id, _cursor(chunk[-1]), ledger.count(run_id))
        processed_any = True

    if not processed_any:
        return _empty_result(engine, ledger, run_id)

    liquidation = 0.0
    if state.open_trade is not None and state.last_price is not None:
        liquidation = _calculate_liquidation_pnl(engine.config, state.open_trade[1], state.last_price)
    if ledger_has_trades:
        all_trades = tuple(ledger.trades(run_id))
    else:
        all_trades = tuple(persisted_trades)
    return _build_result(
        engine.config.initial_capital,
        state.capital + liquidation,
        all_trades,
        state.max_drawdown,
        liquidation,
        state.open_trade is not None,
    )


def _run_chunk(engine: BacktestEngine, events: Iterable[HistoricalRecord], strategy: EventStrategy, state: EventExecutionState, *, price_field: str) -> tuple:
    trades = []
    for record in events:
        if isinstance(record.timestamp_ns, bool) or not isinstance(record.timestamp_ns, int) or record.timestamp_ns < 0:
            raise ValueError("event timestamp_ns must be a non-negative integer")
        if not isinstance(record.instrument, str) or not record.instrument.strip():
            raise ValueError("event instrument is required")
        if not isinstance(record.source, str) or not record.source.strip():
            raise ValueError("event source is required")
        sequence = 0 if record.sequence is None else record.sequence
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("event sequence must be a non-negative integer or None")
        key = (record.timestamp_ns, sequence, record.source, record.instrument, record.timeframe)
        if state.previous_key is not None and key < state.previous_key:
            raise ValueError("events must be ordered deterministically")
        state.previous_key = key

        signal = _normalize_event_signal(
            strategy(EventContext(record.timestamp_ns, record.sequence, record.source, record.instrument, record.payload, record))
        )
        if signal.action in {"HOLD", "NONE"}:
            continue
        price = signal.price
        if price is None:
            raw_price = record.payload.get(price_field)
            if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)) or not isfinite(float(raw_price)):
                raise ValueError(f"event payload must contain finite numeric {price_field!r} or signal price")
            price = float(raw_price)
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not isfinite(float(price)) or price <= 0:
            raise ValueError("event price must be finite and positive")
        price = float(price)
        state.last_price = price

        if state.open_trade is None and signal.action == "BUY":
            execution_price = _execution_price(engine.config, price, "BUY")
            state.capital -= _ensure_cash_available(engine.config, state.capital, execution_price)
            state.open_trade = (record.timestamp_ns, execution_price)
        elif state.open_trade is not None and signal.action == "SELL":
            entry_timestamp, entry_price = state.open_trade
            exit_price = _execution_price(engine.config, price, "SELL")
            trade = _build_trade(engine.config, entry_timestamp, entry_price, record.timestamp_ns, exit_price)
            state.capital += _net_exit_cashflow(engine.config, entry_price, exit_price)
            trades.append(trade)
            state.peak_capital = max(state.peak_capital, state.capital)
            if state.peak_capital > 0:
                state.max_drawdown = max(state.max_drawdown, (state.peak_capital - state.capital) / state.peak_capital)
            state.open_trade = None

        if state.open_trade is not None and state.peak_capital > 0:
            equity = state.capital + _calculate_liquidation_pnl(engine.config, state.open_trade[1], price)
            state.peak_capital = max(state.peak_capital, equity)
            state.max_drawdown = max(state.max_drawdown, (state.peak_capital - equity) / state.peak_capital)
    return tuple(trades)


def _empty_result(engine, ledger, run_id):
    pnl = ledger.net_pnl(run_id)
    initial = engine.config.initial_capital
    return BacktestResult(
        initial_capital=initial,
        final_capital=initial + pnl,
        net_pnl=pnl,
        total_return=pnl / initial,
        trades=tuple(ledger.trades(run_id)) if hasattr(ledger, "trades") else (),
        win_rate=0.0,
        expectancy=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        max_drawdown=0.0,
        cagr=0.0,
    )


def _cursor(record: HistoricalRecord) -> str:
    return f"{record.timestamp_ns}:{record.sequence if record.sequence is not None else 0}:{record.source}:{record.instrument}:{record.timeframe}"


def _legacy_cursor(record: HistoricalRecord) -> str:
    return f"{record.timestamp_ns}:{record.sequence if record.sequence is not None else -1}"
