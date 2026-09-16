"""Incremental high-resolution event backtesting without retaining trade history."""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite, sqrt
from typing import Callable

from app.backtesting.engine import (
    BacktestConfig,
    BacktestResult,
    BacktestTrade,
    EventContext,
    EventStrategy,
    _build_trade,
    _calculate_unrealized_pnl,
    _ensure_cash_available,
    _execution_price,
)
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord

PersistTradeChunk = Callable[[tuple[BacktestTrade, ...], int], object]
_SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60
_NANOSECONDS_PER_YEAR = _SECONDS_PER_YEAR * 1_000_000_000


def _event_order_key(record: HistoricalRecord) -> tuple[object, ...]:
    sequence = record.sequence if record.sequence is not None else -1
    return (record.timestamp_ns, sequence, record.source, record.instrument, record.timeframe)


def run_events_incremental(engine_config: BacktestConfig, events, strategy: EventStrategy, *, persist_chunk: PersistTradeChunk, chunk_size: int = 500, price_field: str = "price") -> BacktestResult:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not isinstance(price_field, str) or not price_field.strip():
        raise ValueError("price_field is required")
    capital = engine_config.initial_capital
    peak_equity = capital
    max_drawdown = 0.0
    open_trade = None
    chunk = []
    chunk_index = 0
    trade_count = wins = 0
    pnl_sum = 0.0
    returns: list[float] = []
    intervals_years: list[float] = []
    first_timestamp = last_timestamp = last_price = None
    previous_key = None
    previous_equity = engine_config.initial_capital
    previous_timestamp = None
    seen_identities = set()
    for record in events:
        if not isinstance(record.timestamp_ns, int) or isinstance(record.timestamp_ns, bool) or record.timestamp_ns < 0:
            raise ValueError("event timestamp_ns must be a non-negative integer")
        identity = record.identity()
        if identity in seen_identities:
            raise ValueError("duplicate event identity")
        seen_identities.add(identity)
        key = _event_order_key(record)
        if previous_key is not None and key <= previous_key:
            raise ValueError("events must be strictly ordered by timestamp, sequence, and stream identity")
        previous_key = key
        first_timestamp = record.timestamp_ns if first_timestamp is None else first_timestamp
        last_timestamp = record.timestamp_ns
        context = EventContext(record.timestamp_ns, record.sequence, record.source, record.instrument, record.payload, record)
        decision = strategy(context)
        action = decision.action.strip().upper() if hasattr(decision, "action") else str(decision or "NONE").strip().upper()
        if action not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("event strategy must return BUY, SELL, HOLD, or NONE")
        signal_price = decision.price if hasattr(decision, "price") else None
        price = signal_price
        source = "signal" if signal_price is not None else f"payload:{price_field}"
        if price is None:
            raw_price = record.payload.get(price_field)
            if raw_price is None and action in {"HOLD", "NONE"}:
                continue
            if not isinstance(raw_price, (int, float)) or isinstance(raw_price, bool):
                raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
            price = float(raw_price)
        if isinstance(price, bool) or not isfinite(float(price)) or float(price) <= 0:
            raise ValueError("event execution price must be finite and positive")
        price = float(price)
        last_price = price
        if open_trade is None and action == "BUY":
            entry_price = _execution_price(engine_config, price, "BUY")
            _ensure_cash_available(engine_config, capital, entry_price)
            open_trade = (record.timestamp_ns, entry_price, price, source, identity)
        elif open_trade is not None and action == "SELL":
            entry_timestamp, entry_price, entry_market_price, entry_source, entry_identity = open_trade
            exit_price = _execution_price(engine_config, price, "SELL")
            trade = _build_trade(engine_config, entry_timestamp, entry_price, record.timestamp_ns, exit_price, entry_market_price=entry_market_price, exit_market_price=price, entry_price_source=entry_source, exit_price_source=source, entry_event_identity=entry_identity, exit_event_identity=identity)
            capital += trade.net_pnl
            trade_count += 1
            wins += int(trade.net_pnl > 0)
            pnl_sum += trade.net_pnl
            chunk.append(trade)
            if len(chunk) >= chunk_size:
                persist_chunk(tuple(chunk), chunk_index)
                chunk_index += 1
                chunk.clear()
            open_trade = None
        equity = capital + (_calculate_unrealized_pnl(engine_config, open_trade[1], price) if open_trade is not None else 0.0)
        if previous_timestamp is not None and record.timestamp_ns > previous_timestamp and previous_equity > 0:
            elapsed_years = (record.timestamp_ns - previous_timestamp) / _NANOSECONDS_PER_YEAR
            value = equity / previous_equity - 1.0
            if elapsed_years > 0 and isfinite(value):
                returns.append(value)
                intervals_years.append(elapsed_years)
        previous_equity = equity
        previous_timestamp = record.timestamp_ns
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity)
    if chunk:
        persist_chunk(tuple(chunk), chunk_index)
    if open_trade is not None and last_price is not None:
        unrealized_pnl = _calculate_unrealized_pnl(engine_config, open_trade[1], last_price)
        final_capital = capital + unrealized_pnl
    else:
        unrealized_pnl = 0.0
        final_capital = capital
    win_rate = wins / trade_count if trade_count else 0.0
    expectancy = pnl_sum / trade_count if trade_count else 0.0
    sharpe = _annualized_ratio(returns, intervals_years, downside_only=False)
    sortino = _annualized_ratio(returns, intervals_years, downside_only=True)
    cagr = _calculate_cagr_from_timestamps(first_timestamp, last_timestamp, engine_config.initial_capital, final_capital)
    return BacktestResult(engine_config.initial_capital, final_capital, final_capital - engine_config.initial_capital, (final_capital - engine_config.initial_capital) / engine_config.initial_capital, (), win_rate, expectancy, sharpe, sortino, max_drawdown, cagr, unrealized_pnl, open_trade is not None)


def _annualized_ratio(returns: list[float], intervals_years: list[float], *, downside_only: bool) -> float | None:
    if len(returns) < 2 or not intervals_years:
        return None
    mean = sum(returns) / len(returns)
    if downside_only:
        denominator = sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    else:
        variance = sum((value - mean) ** 2 for value in returns) / len(returns)
        denominator = sqrt(variance)
    if denominator <= 0:
        return None
    average_interval_years = sum(intervals_years) / len(intervals_years)
    if average_interval_years <= 0:
        return None
    return mean / denominator * sqrt(1.0 / average_interval_years)


def _calculate_cagr_from_timestamps(start: object | None, end: object | None, initial_capital: float, final_capital: float) -> float | None:
    if start is None or end is None or final_capital <= 0 or initial_capital <= 0:
        return None
    if isinstance(start, datetime) or isinstance(end, datetime):
        if not isinstance(start, datetime) or not isinstance(end, datetime): return None
        years = (end - start).total_seconds() / _SECONDS_PER_YEAR
    elif isinstance(start, date) or isinstance(end, date):
        if not isinstance(start, date) or not isinstance(end, date): return None
        years = (end - start).days / 365.25
    elif isinstance(start, (int, float)) and not isinstance(start, bool) and isinstance(end, (int, float)) and not isinstance(end, bool):
        if not isfinite(float(start)) or not isfinite(float(end)): return None
        years = (float(end) - float(start)) / _NANOSECONDS_PER_YEAR
    else:
        return None
    if years <= 0: return None
    return (final_capital / initial_capital) ** (1.0 / years) - 1.0


def run_catalog_events_incremental(catalog: HistoricalCatalog, engine_config: BacktestConfig, *, source: str, instrument: str, strategy: EventStrategy, persist_chunk: PersistTradeChunk, timeframe: str = "tick", start_ns: int | None = None, end_ns: int | None = None, chunk_size: int = 500, price_field: str = "price") -> BacktestResult:
    return run_events_incremental(engine_config, catalog.iter_records(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns), strategy, persist_chunk=persist_chunk, chunk_size=chunk_size, price_field=price_field)