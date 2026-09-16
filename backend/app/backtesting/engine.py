"""Deterministic backtesting engine for bars and high-resolution events."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Callable, Iterable, Mapping, Sequence

from app.algo.strategy import Strategy
from app.backtesting.continuous_futures import ContinuousFuturesRecord, build_continuous_futures_series
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.statistics import EquityPoint, calculate_statistics


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000.0
    quantity: float = 1.0
    slippage_rate: float = 0.0
    transaction_cost_rate: float = 0.0
    quantity_step: float | None = None
    tick_size: float | None = None
    transaction_cost_minimum: float = 0.0
    entry_slippage_rate: float | None = None
    exit_slippage_rate: float | None = None
    execution_timing: str = "close"
    enforce_cash: bool = True

    def __post_init__(self) -> None:
        for name, value in (("initial_capital", self.initial_capital), ("quantity", self.quantity), ("slippage_rate", self.slippage_rate), ("transaction_cost_rate", self.transaction_cost_rate), ("transaction_cost_minimum", self.transaction_cost_minimum)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number")
        for name, value in (("quantity_step", self.quantity_step), ("tick_size", self.tick_size), ("entry_slippage_rate", self.entry_slippage_rate), ("exit_slippage_rate", self.exit_slippage_rate)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value))):
                raise ValueError(f"{name} must be a finite number or None")
        if self.initial_capital <= 0: raise ValueError("initial_capital must be positive")
        if self.quantity <= 0: raise ValueError("quantity must be positive")
        if self.quantity_step is not None and self.quantity_step <= 0: raise ValueError("quantity_step must be positive")
        if self.quantity_step is not None and not _is_multiple(self.quantity, self.quantity_step): raise ValueError("quantity must be an exact multiple of quantity_step")
        if self.tick_size is not None and self.tick_size <= 0: raise ValueError("tick_size must be positive")
        if self.slippage_rate < 0 or self.slippage_rate >= 1: raise ValueError("slippage_rate must be in [0, 1)")
        for name, value in (("entry_slippage_rate", self.entry_slippage_rate), ("exit_slippage_rate", self.exit_slippage_rate)):
            if value is not None and (value < 0 or value >= 1): raise ValueError(f"{name} must be in [0, 1)")
        if self.transaction_cost_rate < 0 or self.transaction_cost_minimum < 0: raise ValueError("transaction costs cannot be negative")
        if self.execution_timing not in {"close", "next_open"}: raise ValueError("execution_timing must be 'close' or 'next_open'")

    @property
    def effective_entry_slippage(self) -> float:
        return self.slippage_rate if self.entry_slippage_rate is None else self.entry_slippage_rate

    @property
    def effective_exit_slippage(self) -> float:
        return self.slippage_rate if self.exit_slippage_rate is None else self.exit_slippage_rate


@dataclass(frozen=True)
class BacktestTrade:
    entry_timestamp: object
    exit_timestamp: object
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    costs: float
    net_pnl: float


@dataclass(frozen=True)
class BacktestResult:
    initial_capital: float
    final_capital: float
    net_pnl: float
    total_return: float
    trades: tuple[BacktestTrade, ...]
    win_rate: float
    expectancy: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown: float
    cagr: float | None
    unrealized_pnl: float = 0.0
    has_open_trade: bool = False
    equity_curve: tuple[EquityPoint, ...] = ()


@dataclass(frozen=True)
class EventContext:
    timestamp_ns: int
    sequence: int | None
    source: str
    instrument: str
    payload: Mapping[str, object]
    record: HistoricalRecord


@dataclass(frozen=True)
class EventSignal:
    action: str
    price: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or self.action.strip().upper() not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("action must be BUY, SELL, HOLD, or NONE")
        if self.price is not None and (isinstance(self.price, bool) or not isinstance(self.price, (int, float)) or not isfinite(float(self.price)) or self.price <= 0):
            raise ValueError("signal price must be finite and positive")


EventStrategy = Callable[[EventContext], EventSignal | str | None]


class BacktestEngine:
    """Run deterministic bar, continuous-futures, or event backtests."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(self, candles: Iterable[Mapping[str, object]], entry_strategy: Strategy, exit_strategy: Strategy) -> BacktestResult:
        capital = self.config.initial_capital
        open_trade: tuple[object, float] | None = None
        trades: list[BacktestTrade] = []
        curve: list[EquityPoint] = []
        peak_equity = capital
        intrabar_drawdown = 0.0
        previous_timestamp = None
        pending_action = None
        pending_timestamp = None
        last_timestamp = None
        last_close = None
        for candle in candles:
            timestamp = _validate_candle(candle, previous_timestamp)
            previous_timestamp = timestamp
            last_timestamp, last_close = timestamp, float(candle["close"])
            context = {key: value for key, value in candle.items() if _is_number(value)}
            if self.config.execution_timing == "next_open" and pending_action is not None:
                if "open" not in candle: raise ValueError("next_open execution requires candle open")
                open_price = float(candle["open"])
                if pending_action == "BUY" and open_trade is None:
                    execution_price = _execution_price(self.config, open_price, "BUY")
                    _ensure_cash_available(self.config, capital, execution_price)
                    open_trade = (pending_timestamp, execution_price)
                elif pending_action == "SELL" and open_trade is not None:
                    entry_timestamp, entry_price = open_trade
                    exit_price = _execution_price(self.config, open_price, "SELL")
                    trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                    capital += trade.net_pnl
                    trades.append(trade)
                    open_trade = None
                pending_action = pending_timestamp = None
            if self.config.execution_timing == "close":
                if open_trade is None and entry_strategy.evaluate(context):
                    execution_price = _execution_price(self.config, last_close, "BUY")
                    _ensure_cash_available(self.config, capital, execution_price)
                    open_trade = (timestamp, execution_price)
                elif open_trade is not None and exit_strategy.evaluate(context):
                    entry_timestamp, entry_price = open_trade
                    exit_price = _execution_price(self.config, last_close, "SELL")
                    trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                    capital += trade.net_pnl
                    trades.append(trade)
                    open_trade = None
            else:
                signal = "SELL" if open_trade is not None and exit_strategy.evaluate(context) else "BUY" if open_trade is None and entry_strategy.evaluate(context) else None
                if signal: pending_action, pending_timestamp = signal, timestamp
            point = _equity_point(timestamp, capital, open_trade, last_close, self.config)
            curve.append(point)
            peak_equity = max(peak_equity, point.equity)
            if open_trade is not None:
                for field, adverse in (("high", "SELL"), ("low", "SELL")):
                    if field not in candle: continue
                    marked = capital + _calculate_unrealized_pnl(self.config, open_trade[1], float(candle[field]))
                    peak_equity = max(peak_equity, marked)
                    if peak_equity > 0: intrabar_drawdown = max(intrabar_drawdown, (peak_equity - marked) / peak_equity)
        if open_trade is not None and last_close is not None and last_timestamp is not None:
            unrealized_pnl = _calculate_unrealized_pnl(self.config, open_trade[1], last_close)
            final_capital = capital + unrealized_pnl
        else:
            unrealized_pnl, final_capital = 0.0, capital
        return _build_result(self.config.initial_capital, final_capital, trades, intrabar_drawdown, unrealized_pnl, open_trade is not None, curve)

    def run_events(self, events: Iterable[HistoricalRecord], strategy: EventStrategy, *, price_field: str = "price") -> BacktestResult:
        if not isinstance(price_field, str) or not price_field.strip(): raise ValueError("price_field is required")
        capital = self.config.initial_capital
        open_trade = None
        trades: list[BacktestTrade] = []
        curve: list[EquityPoint] = []
        previous_key = None
        last_price = last_timestamp = None
        for record in events:
            if not isinstance(record.timestamp_ns, int) or isinstance(record.timestamp_ns, bool) or record.timestamp_ns < 0: raise ValueError("event timestamp_ns must be a non-negative integer")
            if not isinstance(record.instrument, str) or not record.instrument.strip(): raise ValueError("event instrument is required")
            if not isinstance(record.source, str) or not record.source.strip(): raise ValueError("event source is required")
            if record.sequence is not None and (not isinstance(record.sequence, int) or isinstance(record.sequence, bool) or record.sequence < 0): raise ValueError("event sequence must be a non-negative integer or None")
            key = (record.timestamp_ns, record.sequence if record.sequence is not None else -1)
            if previous_key is not None and key <= previous_key: raise ValueError("events must be strictly ordered by timestamp_ns and sequence")
            previous_key = key
            signal = _normalize_event_signal(strategy(EventContext(record.timestamp_ns, record.sequence, record.source, record.instrument, record.payload, record)))
            if signal.action in {"HOLD", "NONE"}: curve.append(_equity_point(record.timestamp_ns, capital, open_trade, last_price or 1.0, self.config)); continue
            price = signal.price
            if price is None:
                raw_price = record.payload.get(price_field)
                if not _is_number(raw_price): raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
                price = float(raw_price)
            if not isfinite(price) or price <= 0: raise ValueError("event execution price must be finite and positive")
            last_price, last_timestamp = price, record.timestamp_ns
            if open_trade is None and signal.action == "BUY":
                execution_price = _execution_price(self.config, price, "BUY")
                _ensure_cash_available(self.config, capital, execution_price)
                open_trade = (record.timestamp_ns, execution_price)
            elif open_trade is not None and signal.action == "SELL":
                entry_timestamp, entry_price = open_trade
                exit_price = _execution_price(self.config, price, "SELL")
                trade = _build_trade(self.config, entry_timestamp, entry_price, record.timestamp_ns, exit_price)
                capital += trade.net_pnl
                trades.append(trade)
                open_trade = None
            curve.append(_equity_point(record.timestamp_ns, capital, open_trade, price, self.config))
        if open_trade is not None and last_price is not None and last_timestamp is not None:
            unrealized_pnl = _calculate_unrealized_pnl(self.config, open_trade[1], last_price)
            final_capital = capital + unrealized_pnl
        else: unrealized_pnl, final_capital = 0.0, capital
        return _build_result(self.config.initial_capital, final_capital, trades, 0.0, unrealized_pnl, open_trade is not None, curve)

    def run_catalog_events(self, catalog: HistoricalCatalog, *, source: str, instrument: str, strategy: EventStrategy, timeframe: str = "tick", start_ns: int | None = None, end_ns: int | None = None, price_field: str = "price") -> BacktestResult:
        return self.run_events(catalog.iter_records(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns), strategy, price_field=price_field)

    def run_continuous_futures(self, windows: Iterable[FNORolloverWindow], records_by_token: Mapping[str, Iterable[HistoricalRecord]], entry_strategy: Strategy, exit_strategy: Strategy) -> BacktestResult:
        return self.run((_continuous_record_to_candle(item) for item in build_continuous_futures_series(windows, records_by_token)), entry_strategy, exit_strategy)

    def run_continuous_futures_events_to_ledger(self, windows, records_by_token, strategy, *, ledger, run_id: str, price_field: str = "close", chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip(): raise ValueError("run_id is required")
        if chunk_size <= 0: raise ValueError("chunk_size must be positive")
        from app.backtesting.event_incremental import run_events_incremental
        series = build_continuous_futures_series(windows, records_by_token)
        return run_events_incremental(self.config, (_continuous_record_to_event(item) for item in series), strategy, persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades), chunk_size=chunk_size, price_field=price_field)

    def run_events_to_ledger(self, events, strategy, *, ledger, run_id: str, price_field: str = "price", chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip(): raise ValueError("run_id is required")
        if chunk_size <= 0: raise ValueError("chunk_size must be positive")
        from app.backtesting.event_incremental import run_events_incremental
        return run_events_incremental(self.config, events, strategy, persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades), chunk_size=chunk_size, price_field=price_field)

    def run_continuous_futures_events(self, windows, records_by_token, strategy, *, price_field: str = "close") -> BacktestResult:
        return self.run_events((_continuous_record_to_event(item) for item in build_continuous_futures_series(windows, records_by_token)), strategy, price_field=price_field)

    def run_incremental_to_ledger(self, candles, entry_strategy, exit_strategy, *, ledger, run_id: str, chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip(): raise ValueError("run_id is required")
        return self.run_incremental(candles, entry_strategy, exit_strategy, persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades), chunk_size=chunk_size)

    def run_incremental(self, candles, entry_strategy, exit_strategy, *, persist_chunk, chunk_size: int = 500) -> BacktestResult:
        if chunk_size <= 0: raise ValueError("chunk_size must be positive")
        capital = self.config.initial_capital
        open_trade = None
        trades: list[BacktestTrade] = []
        curve: list[EquityPoint] = []
        previous_timestamp = None
        last_close = last_timestamp = None
        for candle in candles:
            timestamp = _validate_candle(candle, previous_timestamp)
            previous_timestamp = timestamp
            last_timestamp, last_close = timestamp, float(candle["close"])
            context = {key: value for key, value in candle.items() if _is_number(value)}
            if open_trade is None and entry_strategy.evaluate(context):
                execution_price = _execution_price(self.config, last_close, "BUY")
                _ensure_cash_available(self.config, capital, execution_price)
                open_trade = (timestamp, execution_price)
            elif open_trade is not None and exit_strategy.evaluate(context):
                entry_timestamp, entry_price = open_trade
                exit_price = _execution_price(self.config, last_close, "SELL")
                trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                capital += trade.net_pnl
                trades.append(trade)
                if len(trades) % chunk_size == 0: persist_chunk(tuple(trades[-chunk_size:]), len(trades) // chunk_size - 1)
                open_trade = None
            curve.append(_equity_point(timestamp, capital, open_trade, last_close, self.config))
        if trades and len(trades) % chunk_size: persist_chunk(tuple(trades[-(len(trades) % chunk_size):]), len(trades) // chunk_size)
        if open_trade is not None and last_close is not None:
            unrealized_pnl = _calculate_unrealized_pnl(self.config, open_trade[1], last_close)
            final_capital = capital + unrealized_pnl
        else: unrealized_pnl, final_capital = 0.0, capital
        return _build_result(self.config.initial_capital, final_capital, trades, 0.0, unrealized_pnl, open_trade is not None, curve)


def _validate_candle(candle, previous_timestamp):
    if not isinstance(candle, Mapping): raise ValueError("candle must be a mapping")
    timestamp = candle.get("timestamp")
    if timestamp is None: raise ValueError("candle timestamp is required")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float, date, datetime)): raise ValueError("candle timestamp must be an int, float, date, or datetime")
    if isinstance(timestamp, (int, float)) and not isfinite(float(timestamp)): raise ValueError("candle timestamp must be finite")
    if previous_timestamp is not None:
        try:
            if timestamp <= previous_timestamp: raise ValueError("candles must have strictly increasing timestamps")
        except TypeError as exc: raise ValueError("candle timestamps must use one comparable type") from exc
    if "close" not in candle: raise ValueError("candle close is required")
    _validate_price_field(candle["close"], "close")
    for field in ("open", "high", "low"):
        if field in candle: _validate_price_field(candle[field], field)
    return timestamp


def _validate_price_field(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)) or float(value) <= 0: raise ValueError(f"candle {field} must be finite and positive")


def _execution_price(config, market_price, side):
    slippage = config.effective_entry_slippage if side == "BUY" else config.effective_exit_slippage
    raw = market_price * (1.0 + slippage if side == "BUY" else 1.0 - slippage)
    return _round_price(raw, config.tick_size)


def _round_price(price, tick_size):
    if tick_size is None: return price
    value = (Decimal(str(price)) / Decimal(str(tick_size))).quantize(Decimal("1"), rounding="ROUND_HALF_UP") * Decimal(str(tick_size))
    rounded = float(value)
    if not isfinite(rounded) or rounded <= 0: raise ValueError("execution price is invalid after tick-size rounding")
    return rounded


def _ensure_cash_available(config, capital, entry_price):
    if not config.enforce_cash: return
    entry_value = entry_price * config.quantity
    estimated_cost = max(config.transaction_cost_minimum, entry_value * config.transaction_cost_rate)
    if not isfinite(entry_value + estimated_cost) or entry_value + estimated_cost > capital: raise ValueError("insufficient cash for backtest entry")


def _is_multiple(value, step):
    quotient = Decimal(str(value)) / Decimal(str(step))
    return quotient == quotient.to_integral_value()


def _timestamp_ns(value):
    if isinstance(value, datetime):
        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, date): return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)): return int(value)
    raise ValueError("timestamp must be date, datetime, or numeric nanoseconds")


def _equity_point(timestamp, capital, open_trade, mark_price, config):
    unrealized = _calculate_unrealized_pnl(config, open_trade[1], mark_price) if open_trade is not None else 0.0
    return EquityPoint(_timestamp_ns(timestamp), capital + unrealized, capital - config.initial_capital, unrealized)


def _continuous_record_to_candle(item):
    candle = dict(item.payload); candle["timestamp"] = item.timestamp_ns; candle["contract_token"] = item.contract_token; return candle


def _continuous_record_to_event(item):
    payload = dict(item.payload); payload["contract_token"] = item.contract_token; payload["continuous_underlying"] = item.underlying; payload["instrument_type"] = item.instrument_type
    return HistoricalRecord(source=item.record.source, instrument=item.record.instrument, timeframe=item.record.timeframe, timestamp_ns=item.timestamp_ns, payload=payload, sequence=item.record.sequence)


def _normalize_event_signal(decision):
    if decision is None: return EventSignal("NONE")
    if isinstance(decision, EventSignal): return EventSignal(decision.action.strip().upper(), decision.price)
    if isinstance(decision, str): return EventSignal(decision.strip().upper())
    raise TypeError("event strategy must return EventSignal, action string, or None")


def _build_trade(config, entry_timestamp, entry_price, exit_timestamp, exit_price):
    gross_pnl = (exit_price - entry_price) * config.quantity
    traded_value = (entry_price + exit_price) * config.quantity
    costs = max(config.transaction_cost_minimum, traded_value * config.transaction_cost_rate)
    values = (entry_price, exit_price, gross_pnl, traded_value, costs, gross_pnl - costs)
    if not all(isfinite(float(value)) for value in values): raise ValueError("backtest trade values must be finite")
    return BacktestTrade(entry_timestamp, exit_timestamp, entry_price, exit_price, config.quantity, gross_pnl, costs, gross_pnl - costs)


def _calculate_unrealized_pnl(config, entry_price, mark_price):
    exit_price = _execution_price(config, mark_price, "SELL")
    gross_pnl = (exit_price - entry_price) * config.quantity
    traded_value = (entry_price + exit_price) * config.quantity
    costs = max(config.transaction_cost_minimum, traded_value * config.transaction_cost_rate)
    unrealized = gross_pnl - costs
    if not all(isfinite(float(value)) for value in (entry_price, mark_price, exit_price, gross_pnl, costs, unrealized)): raise ValueError("unrealized backtest values must be finite")
    return unrealized


def _build_result(initial_capital, final_capital, trades, max_drawdown, unrealized_pnl=0.0, has_open_trade=False, equity_curve=()):
    wins = sum(1 for trade in trades if trade.net_pnl > 0)
    net_pnl = final_capital - initial_capital
    curve = tuple(equity_curve)
    if curve and has_open_trade and curve[-1].equity != final_capital:
        curve = curve + (EquityPoint(curve[-1].timestamp_ns + 1, final_capital, net_pnl - unrealized_pnl, unrealized_pnl),)
    stats = calculate_statistics(curve, initial_capital) if curve else None
    calculated_drawdown = stats.max_drawdown if stats else 0.0
    return BacktestResult(initial_capital, final_capital, net_pnl, net_pnl / initial_capital, tuple(trades), wins / len(trades) if trades else 0.0, net_pnl / len(trades) if trades else 0.0, stats.sharpe_ratio if stats else None, stats.sortino_ratio if stats else None, max(calculated_drawdown, max_drawdown), stats.cagr if stats else None, unrealized_pnl, has_open_trade, curve)


def _calculate_cagr(trades, initial_capital, final_capital):
    if not trades or final_capital <= 0: return None
    points = []
    capital = initial_capital
    for trade in trades:
        capital += trade.net_pnl
        points.append(EquityPoint(_timestamp_ns(trade.exit_timestamp), capital, capital - initial_capital, 0.0))
    return calculate_statistics(points, initial_capital).cagr


def _calculate_cagr_from_timestamps(start, end, initial_capital, final_capital):
    if start is None or end is None: return None
    return calculate_statistics((EquityPoint(_timestamp_ns(start), initial_capital, 0.0, 0.0), EquityPoint(_timestamp_ns(end), final_capital, final_capital - initial_capital, 0.0)), initial_capital).cagr


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))
