"""Deterministic backtesting engine for bars and high-resolution events."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite, sqrt
from typing import Callable, Iterable, Mapping, Sequence

from app.algo.strategy import Strategy
from app.backtesting.continuous_futures import ContinuousFuturesRecord, build_continuous_futures_series
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


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
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.quantity_step is not None and self.quantity_step <= 0:
            raise ValueError("quantity_step must be positive")
        if self.quantity_step is not None and not _is_multiple(self.quantity, self.quantity_step):
            raise ValueError("quantity must be an exact multiple of quantity_step")
        if self.tick_size is not None and self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.slippage_rate < 0 or self.slippage_rate >= 1:
            raise ValueError("slippage_rate must be in [0, 1)")
        for name, value in (("entry_slippage_rate", self.entry_slippage_rate), ("exit_slippage_rate", self.exit_slippage_rate)):
            if value is not None and (value < 0 or value >= 1):
                raise ValueError(f"{name} must be in [0, 1)")
        if self.transaction_cost_rate < 0:
            raise ValueError("transaction_cost_rate cannot be negative")
        if self.transaction_cost_minimum < 0:
            raise ValueError("transaction_cost_minimum cannot be negative")
        if self.execution_timing not in {"close", "next_open"}:
            raise ValueError("execution_timing must be 'close' or 'next_open'")

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
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    cagr: float
    unrealized_pnl: float = 0.0
    has_open_trade: bool = False


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
        if self.price is not None:
            if isinstance(self.price, bool) or not isinstance(self.price, (int, float)) or not isfinite(float(self.price)):
                raise ValueError("signal price must be a finite number")
            if self.price <= 0:
                raise ValueError("signal price must be positive")


EventStrategy = Callable[[EventContext], EventSignal | str | None]


class BacktestEngine:
    """Run deterministic bar, continuous-futures, or event backtests."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(self, candles: Iterable[Mapping[str, object]], entry_strategy: Strategy, exit_strategy: Strategy) -> BacktestResult:
        capital = self.config.initial_capital
        peak_capital = capital
        max_drawdown = 0.0
        open_trade: tuple[object, float] | None = None
        trades: list[BacktestTrade] = []
        previous_timestamp = None
        last_close = None
        last_timestamp = None
        pending_action: str | None = None
        pending_timestamp = None
        for candle in candles:
            timestamp = _validate_candle(candle, previous_timestamp)
            previous_timestamp = timestamp
            last_timestamp = timestamp
            close = float(candle["close"])
            last_close = close
            context = {key: value for key, value in candle.items() if _is_number(value)}
            if self.config.execution_timing == "next_open" and pending_action is not None:
                if "open" not in candle:
                    raise ValueError("next_open execution requires candle open")
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
                    peak_capital = max(peak_capital, capital)
                    max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
                    trades.append(trade)
                    open_trade = None
                pending_action = None
                pending_timestamp = None
            if open_trade is not None:
                intrabar_low = candle.get("low")
                if intrabar_low is not None:
                    _validate_price_field(intrabar_low, "low")
                    adverse_price = _execution_price(self.config, float(intrabar_low), "SELL")
                    marked_capital = capital + (adverse_price - open_trade[1]) * self.config.quantity
                    peak_capital = max(peak_capital, capital)
                    if marked_capital < capital:
                        max_drawdown = max(max_drawdown, (capital - marked_capital) / capital)
            if self.config.execution_timing == "close":
                if open_trade is None and entry_strategy.evaluate(context):
                    execution_price = _execution_price(self.config, close, "BUY")
                    _ensure_cash_available(self.config, capital, execution_price)
                    open_trade = (timestamp, execution_price)
                elif open_trade is not None and exit_strategy.evaluate(context):
                    entry_timestamp, entry_price = open_trade
                    exit_price = _execution_price(self.config, close, "SELL")
                    trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                    capital += trade.net_pnl
                    peak_capital = max(peak_capital, capital)
                    max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
                    trades.append(trade)
                    open_trade = None
            else:
                signal = "SELL" if open_trade is not None and exit_strategy.evaluate(context) else "BUY" if open_trade is None and entry_strategy.evaluate(context) else None
                if signal is not None:
                    pending_action = signal
                    pending_timestamp = timestamp
        if open_trade is not None and last_close is not None and last_timestamp is not None:
            unrealized_pnl = _calculate_unrealized_pnl(self.config, open_trade[1], last_close)
            final_capital = capital + unrealized_pnl
            peak_capital = max(peak_capital, final_capital)
            max_drawdown = max(max_drawdown, (peak_capital - final_capital) / peak_capital)
        else:
            unrealized_pnl = 0.0
            final_capital = capital
        return _build_result(self.config.initial_capital, final_capital, trades, max_drawdown, unrealized_pnl, open_trade is not None)

    def run_events(self, events: Iterable[HistoricalRecord], strategy: EventStrategy, *, price_field: str = "price") -> BacktestResult:
        if not isinstance(price_field, str) or not price_field.strip():
            raise ValueError("price_field is required")
        capital = self.config.initial_capital
        peak_capital = capital
        max_drawdown = 0.0
        open_trade: tuple[object, float] | None = None
        trades: list[BacktestTrade] = []
        previous_key: tuple[int, int] | None = None
        last_price = None
        last_timestamp = None
        for record in events:
            if not isinstance(record.timestamp_ns, int) or isinstance(record.timestamp_ns, bool) or record.timestamp_ns < 0:
                raise ValueError("event timestamp_ns must be a non-negative integer")
            if not isinstance(record.instrument, str) or not record.instrument.strip():
                raise ValueError("event instrument is required")
            if not isinstance(record.source, str) or not record.source.strip():
                raise ValueError("event source is required")
            if record.sequence is not None and (not isinstance(record.sequence, int) or isinstance(record.sequence, bool) or record.sequence < 0):
                raise ValueError("event sequence must be a non-negative integer or None")
            key = (record.timestamp_ns, record.sequence if record.sequence is not None else -1)
            if previous_key is not None and key <= previous_key:
                raise ValueError("events must be strictly ordered by timestamp_ns and sequence")
            previous_key = key
            signal = _normalize_event_signal(strategy(EventContext(record.timestamp_ns, record.sequence, record.source, record.instrument, record.payload, record)))
            if signal.action in {"HOLD", "NONE"}:
                continue
            price = signal.price
            if price is None:
                raw_price = record.payload.get(price_field)
                if not _is_number(raw_price):
                    raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
                price = float(raw_price)
            if not isfinite(price) or price <= 0:
                raise ValueError("event execution price must be finite and positive")
            last_price = price
            last_timestamp = record.timestamp_ns
            if open_trade is None and signal.action == "BUY":
                execution_price = _execution_price(self.config, price, "BUY")
                _ensure_cash_available(self.config, capital, execution_price)
                open_trade = (record.timestamp_ns, execution_price)
            elif open_trade is not None and signal.action == "SELL":
                entry_timestamp, entry_price = open_trade
                exit_price = _execution_price(self.config, price, "SELL")
                trade = _build_trade(self.config, entry_timestamp, entry_price, record.timestamp_ns, exit_price)
                capital += trade.net_pnl
                peak_capital = max(peak_capital, capital)
                max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
                trades.append(trade)
                open_trade = None
        if open_trade is not None and last_price is not None and last_timestamp is not None:
            unrealized_pnl = _calculate_unrealized_pnl(self.config, open_trade[1], last_price)
            final_capital = capital + unrealized_pnl
            peak_capital = max(peak_capital, final_capital)
            max_drawdown = max(max_drawdown, (peak_capital - final_capital) / peak_capital)
        else:
            unrealized_pnl = 0.0
            final_capital = capital
        return _build_result(self.config.initial_capital, final_capital, trades, max_drawdown, unrealized_pnl, open_trade is not None)

    def run_catalog_events(self, catalog: HistoricalCatalog, *, source: str, instrument: str, strategy: EventStrategy, timeframe: str = "tick", start_ns: int | None = None, end_ns: int | None = None, price_field: str = "price") -> BacktestResult:
        return self.run_events(catalog.iter_records(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns), strategy, price_field=price_field)

    def run_continuous_futures(self, windows: Iterable[FNORolloverWindow], records_by_token: Mapping[str, Iterable[HistoricalRecord]], entry_strategy: Strategy, exit_strategy: Strategy) -> BacktestResult:
        series = build_continuous_futures_series(windows, records_by_token)
        return self.run((_continuous_record_to_candle(item) for item in series), entry_strategy, exit_strategy)

    def run_continuous_futures_events_to_ledger(self, windows: Iterable[FNORolloverWindow], records_by_token: Mapping[str, Iterable[HistoricalRecord]], strategy: EventStrategy, *, ledger, run_id: str, price_field: str = "close", chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        from app.backtesting.event_incremental import run_events_incremental
        series = build_continuous_futures_series(windows, records_by_token)
        return run_events_incremental(self.config, (_continuous_record_to_event(item) for item in series), strategy, persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades), chunk_size=chunk_size, price_field=price_field)

    def run_events_to_ledger(self, events: Iterable[HistoricalRecord], strategy: EventStrategy, *, ledger, run_id: str, price_field: str = "price", chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        from app.backtesting.event_incremental import run_events_incremental
        return run_events_incremental(self.config, events, strategy, persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades), chunk_size=chunk_size, price_field=price_field)

    def run_continuous_futures_events(self, windows: Iterable[FNORolloverWindow], records_by_token: Mapping[str, Iterable[HistoricalRecord]], strategy: EventStrategy, *, price_field: str = "close") -> BacktestResult:
        series = build_continuous_futures_series(windows, records_by_token)
        return self.run_events((_continuous_record_to_event(item) for item in series), strategy, price_field=price_field)

    def run_incremental_to_ledger(self, candles: Iterable[Mapping[str, object]], entry_strategy: Strategy, exit_strategy: Strategy, *, ledger, run_id: str, chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip():
            raise ValueError("run_id is required")
        return self.run_incremental(candles, entry_strategy, exit_strategy, persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades), chunk_size=chunk_size)

    def run_incremental(self, candles: Iterable[Mapping[str, object]], entry_strategy: Strategy, exit_strategy: Strategy, *, persist_chunk: Callable[[Sequence[BacktestTrade], int], object], chunk_size: int = 500) -> BacktestResult:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        capital = self.config.initial_capital
        peak_capital = capital
        max_drawdown = 0.0
        open_trade: tuple[object, float] | None = None
        chunk: list[BacktestTrade] = []
        chunk_index = 0
        trade_count = wins = 0
        pnl_sum = return_sum = return_square_sum = downside_square_sum = 0.0
        first_entry = last_exit = None
        last_close = None
        last_timestamp = None
        previous_timestamp = None
        for candle in candles:
            timestamp = _validate_candle(candle, previous_timestamp)
            previous_timestamp = timestamp
            last_timestamp = timestamp
            close = float(candle["close"])
            last_close = close
            context = {key: value for key, value in candle.items() if _is_number(value)}
            if open_trade is None and entry_strategy.evaluate(context):
                execution_price = _execution_price(self.config, close, "BUY")
                _ensure_cash_available(self.config, capital, execution_price)
                open_trade = (timestamp, execution_price)
            elif open_trade is not None and exit_strategy.evaluate(context):
                entry_timestamp, entry_price = open_trade
                exit_price = _execution_price(self.config, close, "SELL")
                trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                capital += trade.net_pnl
                peak_capital = max(peak_capital, capital)
                max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
                trade_count += 1
                wins += int(trade.net_pnl > 0)
                pnl_sum += trade.net_pnl
                trade_return = trade.net_pnl / self.config.initial_capital
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
        if open_trade is not None and last_close is not None and last_timestamp is not None:
            unrealized_pnl = _calculate_unrealized_pnl(self.config, open_trade[1], last_close)
            final_capital = capital + unrealized_pnl
            peak_capital = max(peak_capital, final_capital)
            max_drawdown = max(max_drawdown, (peak_capital - final_capital) / peak_capital)
            if first_entry is None:
                first_entry = open_trade[0]
            cagr_end = last_timestamp
        else:
            unrealized_pnl = 0.0
            final_capital = capital
            cagr_end = last_exit
        mean_return = return_sum / trade_count if trade_count else 0.0
        variance = max(return_square_sum / trade_count - mean_return * mean_return, 0.0) if trade_count else 0.0
        downside = sqrt(downside_square_sum / trade_count) if trade_count else 0.0
        return BacktestResult(self.config.initial_capital, final_capital, final_capital - self.config.initial_capital, (final_capital - self.config.initial_capital) / self.config.initial_capital, (), wins / trade_count if trade_count else 0.0, pnl_sum / trade_count if trade_count else 0.0, mean_return / sqrt(variance) if variance > 0 else 0.0, mean_return / downside if downside > 0 else 0.0, max_drawdown, _calculate_cagr_from_timestamps(first_entry, cagr_end, self.config.initial_capital, final_capital), unrealized_pnl, open_trade is not None)


def _validate_candle(candle: Mapping[str, object], previous_timestamp: object | None) -> object:
    if not isinstance(candle, Mapping):
        raise ValueError("candle must be a mapping")
    timestamp = candle.get("timestamp")
    if timestamp is None:
        raise ValueError("candle timestamp is required")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float, date, datetime)):
        raise ValueError("candle timestamp must be an int, float, date, or datetime")
    if isinstance(timestamp, (int, float)) and not isfinite(float(timestamp)):
        raise ValueError("candle timestamp must be finite")
    if previous_timestamp is not None:
        try:
            if timestamp <= previous_timestamp:
                raise ValueError("candles must have strictly increasing timestamps")
        except TypeError as exc:
            raise ValueError("candle timestamps must use one comparable type") from exc
    if "close" not in candle:
        raise ValueError("candle close is required")
    _validate_price_field(candle["close"], "close")
    for field in ("open", "high", "low"):
        if field in candle:
            _validate_price_field(candle[field], field)
    return timestamp


def _validate_price_field(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"candle {field} must be finite and positive")


def _execution_price(config: BacktestConfig, market_price: float, side: str) -> float:
    slippage = config.effective_entry_slippage if side == "BUY" else config.effective_exit_slippage
    raw = market_price * (1.0 + slippage if side == "BUY" else 1.0 - slippage)
    return _round_price(raw, config.tick_size)


def _round_price(price: float, tick_size: float | None) -> float:
    if tick_size is None:
        return price
    value = (Decimal(str(price)) / Decimal(str(tick_size))).quantize(Decimal("1"), rounding="ROUND_HALF_UP") * Decimal(str(tick_size))
    rounded = float(value)
    if not isfinite(rounded) or rounded <= 0:
        raise ValueError("execution price is invalid after tick-size rounding")
    return rounded


def _ensure_cash_available(config: BacktestConfig, capital: float, entry_price: float) -> None:
    if not config.enforce_cash:
        return
    entry_value = entry_price * config.quantity
    estimated_cost = max(config.transaction_cost_minimum, entry_value * config.transaction_cost_rate)
    if not isfinite(entry_value + estimated_cost) or entry_value + estimated_cost > capital:
        raise ValueError("insufficient cash for backtest entry")


def _is_multiple(value: float, step: float) -> bool:
    quotient = Decimal(str(value)) / Decimal(str(step))
    return quotient == quotient.to_integral_value()


def _continuous_record_to_candle(item: ContinuousFuturesRecord) -> dict[str, object]:
    candle = dict(item.payload)
    candle["timestamp"] = item.timestamp_ns
    candle["contract_token"] = item.contract_token
    return candle


def _continuous_record_to_event(item: ContinuousFuturesRecord) -> HistoricalRecord:
    payload = dict(item.payload)
    payload["contract_token"] = item.contract_token
    payload["continuous_underlying"] = item.underlying
    payload["instrument_type"] = item.instrument_type
    return HistoricalRecord(source=item.record.source, instrument=item.record.instrument, timeframe=item.record.timeframe, timestamp_ns=item.timestamp_ns, payload=payload, sequence=item.record.sequence)


def _normalize_event_signal(decision: EventSignal | str | None) -> EventSignal:
    if decision is None:
        return EventSignal("NONE")
    if isinstance(decision, EventSignal):
        return EventSignal(decision.action.strip().upper(), decision.price)
    if isinstance(decision, str):
        return EventSignal(decision.strip().upper())
    raise TypeError("event strategy must return EventSignal, action string, or None")


def _build_trade(config: BacktestConfig, entry_timestamp: object, entry_price: float, exit_timestamp: object, exit_price: float) -> BacktestTrade:
    gross_pnl = (exit_price - entry_price) * config.quantity
    traded_value = (entry_price + exit_price) * config.quantity
    costs = max(config.transaction_cost_minimum, traded_value * config.transaction_cost_rate)
    values = (entry_price, exit_price, gross_pnl, traded_value, costs, gross_pnl - costs)
    if not all(isfinite(float(value)) for value in values):
        raise ValueError("backtest trade values must be finite")
    return BacktestTrade(entry_timestamp, exit_timestamp, entry_price, exit_price, config.quantity, gross_pnl, costs, gross_pnl - costs)


def _calculate_unrealized_pnl(config: BacktestConfig, entry_price: float, mark_price: float) -> float:
    exit_price = _execution_price(config, mark_price, "SELL")
    gross_pnl = (exit_price - entry_price) * config.quantity
    traded_value = (entry_price + exit_price) * config.quantity
    costs = max(config.transaction_cost_minimum, traded_value * config.transaction_cost_rate)
    unrealized = gross_pnl - costs
    if not all(isfinite(float(value)) for value in (entry_price, mark_price, exit_price, gross_pnl, costs, unrealized)):
        raise ValueError("unrealized backtest values must be finite")
    return unrealized


def _build_result(initial_capital: float, final_capital: float, trades: list[BacktestTrade], max_drawdown: float, unrealized_pnl: float = 0.0, has_open_trade: bool = False) -> BacktestResult:
    wins = sum(1 for trade in trades if trade.net_pnl > 0)
    net_pnl = final_capital - initial_capital
    return BacktestResult(initial_capital, final_capital, net_pnl, net_pnl / initial_capital, tuple(trades), wins / len(trades) if trades else 0.0, net_pnl / len(trades) if trades else 0.0, _trade_sharpe_ratio(trades, initial_capital), _trade_sortino_ratio(trades, initial_capital), max_drawdown, _calculate_cagr(trades, initial_capital, final_capital), unrealized_pnl, has_open_trade)


def _trade_sharpe_ratio(trades: list[BacktestTrade], initial_capital: float) -> float:
    if len(trades) < 2:
        return 0.0
    returns = [trade.net_pnl / initial_capital for trade in trades]
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
    return mean_return / sqrt(variance) if variance else 0.0


def _trade_sortino_ratio(trades: list[BacktestTrade], initial_capital: float) -> float:
    if len(trades) < 2:
        return 0.0
    returns = [trade.net_pnl / initial_capital for trade in trades]
    mean_return = sum(returns) / len(returns)
    downside = sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    return mean_return / downside if downside else 0.0


def _calculate_cagr(trades: list[BacktestTrade], initial_capital: float, final_capital: float) -> float:
    if not trades or final_capital <= 0:
        return 0.0
    start, end = trades[0].entry_timestamp, trades[-1].exit_timestamp
    if not isinstance(start, (datetime, date)) or not isinstance(end, type(start)):
        return 0.0
    years = (end - start).total_seconds() / (365.25 * 24 * 60 * 60) if isinstance(start, datetime) else (end - start).days / 365.25
    return (final_capital / initial_capital) ** (1.0 / years) - 1.0 if years > 0 else 0.0


def _calculate_cagr_from_timestamps(start: object | None, end: object | None, initial_capital: float, final_capital: float) -> float:
    if start is None or end is None or final_capital <= 0 or initial_capital <= 0:
        return 0.0
    if isinstance(start, datetime) or isinstance(end, datetime):
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            return 0.0
        years = (end - start).total_seconds() / (365.25 * 24 * 60 * 60)
    elif isinstance(start, date) or isinstance(end, date):
        if not isinstance(start, date) or not isinstance(end, date):
            return 0.0
        years = (end - start).days / 365.25
    elif isinstance(start, (int, float)) and not isinstance(start, bool) and isinstance(end, (int, float)) and not isinstance(end, bool):
        if not isfinite(float(start)) or not isfinite(float(end)):
            return 0.0
        elapsed_ns = float(end) - float(start)
        years = elapsed_ns / (365.25 * 24 * 60 * 60 * 1_000_000_000)
    else:
        return 0.0
    return (final_capital / initial_capital) ** (1.0 / years) - 1.0 if years > 0 else 0.0


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))
