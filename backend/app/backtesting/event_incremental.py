"""Incremental high-resolution event backtesting without retaining trade history."""

from __future__ import annotations

from math import sqrt
from typing import Callable

from app.backtesting.engine import BacktestConfig, BacktestResult, BacktestTrade, EventContext, EventStrategy, _build_trade, _calculate_cagr_from_timestamps
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


PersistTradeChunk = Callable[[tuple[BacktestTrade, ...], int], object]


def run_events_incremental(
    engine_config: BacktestConfig,
    events: list[HistoricalRecord] | tuple[HistoricalRecord, ...] | object,
    strategy: EventStrategy,
    *,
    persist_chunk: PersistTradeChunk,
    chunk_size: int = 500,
    price_field: str = "price",
) -> BacktestResult:
    """Run events incrementally and persist completed trades in bounded chunks.

    The event iterable is consumed once. Only the currently open trade and the
    current trade chunk remain in memory; the complete event stream and trade
    ledger are never materialized by this runner.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not price_field.strip():
        raise ValueError("price_field is required")

    capital = engine_config.initial_capital
    peak_capital = capital
    max_drawdown = 0.0
    open_trade: tuple[int, float] | None = None
    chunk: list[BacktestTrade] = []
    chunk_index = 0
    trade_count = 0
    wins = 0
    pnl_sum = 0.0
    return_sum = 0.0
    return_square_sum = 0.0
    downside_square_sum = 0.0
    first_entry: int | None = None
    last_exit: int | None = None
    previous_key: tuple[int, int] | None = None

    for record in events:
        sequence_key = record.sequence if record.sequence is not None else -1
        key = (record.timestamp_ns, sequence_key)
        if previous_key is not None and key < previous_key:
            raise ValueError("events must be ordered by timestamp_ns and sequence")
        previous_key = key

        context = EventContext(
            timestamp_ns=record.timestamp_ns,
            sequence=record.sequence,
            source=record.source,
            instrument=record.instrument,
            payload=record.payload,
            record=record,
        )
        decision = strategy(context)
        action = decision.action.upper() if hasattr(decision, "action") else str(decision or "NONE").upper()
        if action in {"HOLD", "NONE"}:
            continue
        signal_price = decision.price if hasattr(decision, "price") else None
        price = signal_price
        if price is None:
            raw_price = record.payload.get(price_field)
            if not isinstance(raw_price, (int, float)) or isinstance(raw_price, bool):
                raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
            price = float(raw_price)
        if price <= 0:
            raise ValueError("event execution price must be positive")

        if open_trade is None and action == "BUY":
            open_trade = (record.timestamp_ns, price * (1.0 + engine_config.slippage_rate))
        elif open_trade is not None and action == "SELL":
            entry_timestamp, entry_price = open_trade
            exit_price = price * (1.0 - engine_config.slippage_rate)
            trade = _build_trade(engine_config, entry_timestamp, entry_price, record.timestamp_ns, exit_price)
            capital += trade.net_pnl
            peak_capital = max(peak_capital, capital)
            max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
            trade_count += 1
            wins += int(trade.net_pnl > 0)
            pnl_sum += trade.net_pnl
            trade_return = trade.net_pnl / engine_config.initial_capital
            return_sum += trade_return
            return_square_sum += trade_return * trade_return
            downside_square_sum += min(trade_return, 0.0) ** 2
            first_entry = trade.entry_timestamp if first_entry is None else first_entry
            last_exit = trade.exit_timestamp
            chunk.append(trade)
            if len(chunk) >= chunk_size:
                persist_chunk(tuple(chunk), chunk_index)
                chunk_index += 1
                chunk.clear()
            open_trade = None

    if chunk:
        persist_chunk(tuple(chunk), chunk_index)

    win_rate = wins / trade_count if trade_count else 0.0
    expectancy = pnl_sum / trade_count if trade_count else 0.0
    mean_return = return_sum / trade_count if trade_count else 0.0
    variance = max(return_square_sum / trade_count - mean_return * mean_return, 0.0) if trade_count else 0.0
    sharpe = mean_return / sqrt(variance) if variance > 0 else 0.0
    downside = sqrt(downside_square_sum / trade_count) if trade_count else 0.0
    sortino = mean_return / downside if downside > 0 else 0.0
    cagr = _calculate_cagr_from_timestamps(first_entry, last_exit, engine_config.initial_capital, capital)
    return BacktestResult(
        initial_capital=engine_config.initial_capital,
        final_capital=capital,
        net_pnl=capital - engine_config.initial_capital,
        total_return=(capital - engine_config.initial_capital) / engine_config.initial_capital,
        trades=(),
        win_rate=win_rate,
        expectancy=expectancy,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_drawdown,
        cagr=cagr,
    )


def run_catalog_events_incremental(
    catalog: HistoricalCatalog,
    engine_config: BacktestConfig,
    *,
    source: str,
    instrument: str,
    strategy: EventStrategy,
    persist_chunk: PersistTradeChunk,
    timeframe: str = "tick",
    start_ns: int | None = None,
    end_ns: int | None = None,
    chunk_size: int = 500,
    price_field: str = "price",
) -> BacktestResult:
    """Stream catalog events into bounded-chunk event backtesting."""
    return run_events_incremental(
        engine_config,
        catalog.events(source, instrument, timeframe, start_ns, end_ns),
        strategy,
        persist_chunk=persist_chunk,
        chunk_size=chunk_size,
        price_field=price_field,
    )
