"""Deterministic backtesting engine for bars and high-resolution events."""

from dataclasses import dataclass
from datetime import date, datetime
from math import sqrt
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

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.slippage_rate < 0 or self.transaction_cost_rate < 0:
            raise ValueError("cost rates cannot be negative")


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
        if self.action.upper() not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("action must be BUY, SELL, HOLD, or NONE")
        if self.price is not None and self.price <= 0:
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
        for candle in candles:
            timestamp = candle.get("timestamp")
            close = float(candle["close"])
            if close <= 0:
                raise ValueError("candle close must be positive")
            context = {key: float(value) for key, value in candle.items() if _is_number(value)}
            if open_trade is None and entry_strategy.evaluate(context):
                open_trade = (timestamp, close * (1.0 + self.config.slippage_rate))
            elif open_trade is not None and exit_strategy.evaluate(context):
                entry_timestamp, entry_price = open_trade
                exit_price = close * (1.0 - self.config.slippage_rate)
                trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                capital += trade.net_pnl
                peak_capital = max(peak_capital, capital)
                max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
                trades.append(trade)
                open_trade = None
        return _build_result(self.config.initial_capital, capital, trades, max_drawdown)

    def run_events(self, events: Iterable[HistoricalRecord], strategy: EventStrategy, *, price_field: str = "price") -> BacktestResult:
        if not price_field.strip():
            raise ValueError("price_field is required")
        capital = self.config.initial_capital
        peak_capital = capital
        max_drawdown = 0.0
        open_trade: tuple[object, float] | None = None
        trades: list[BacktestTrade] = []
        previous_key: tuple[int, int] | None = None
        for record in events:
            if record.timestamp_ns < 0:
                raise ValueError("event timestamp_ns cannot be negative")
            key = (record.timestamp_ns, record.sequence if record.sequence is not None else -1)
            if previous_key is not None and key < previous_key:
                raise ValueError("events must be ordered by timestamp_ns and sequence")
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
            if open_trade is None and signal.action == "BUY":
                open_trade = (record.timestamp_ns, price * (1.0 + self.config.slippage_rate))
            elif open_trade is not None and signal.action == "SELL":
                entry_timestamp, entry_price = open_trade
                exit_price = price * (1.0 - self.config.slippage_rate)
                trade = _build_trade(self.config, entry_timestamp, entry_price, record.timestamp_ns, exit_price)
                capital += trade.net_pnl
                peak_capital = max(peak_capital, capital)
                max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
                trades.append(trade)
                open_trade = None
        return _build_result(self.config.initial_capital, capital, trades, max_drawdown)

    def run_catalog_events(self, catalog: HistoricalCatalog, *, source: str, instrument: str, strategy: EventStrategy, timeframe: str = "tick", start_ns: int | None = None, end_ns: int | None = None, price_field: str = "price") -> BacktestResult:
        return self.run_events(catalog.events(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns), strategy, price_field=price_field)

    def run_continuous_futures(self, windows: Iterable[FNORolloverWindow], records_by_token: Mapping[str, Iterable[HistoricalRecord]], entry_strategy: Strategy, exit_strategy: Strategy) -> BacktestResult:
        series = build_continuous_futures_series(windows, records_by_token)
        return self.run((_continuous_record_to_candle(item) for item in series), entry_strategy, exit_strategy)

    def run_continuous_futures_events_to_ledger(self, windows: Iterable[FNORolloverWindow], records_by_token: Mapping[str, Iterable[HistoricalRecord]], strategy: EventStrategy, *, ledger, run_id: str, price_field: str = "close", chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        series = build_continuous_futures_series(windows, records_by_token)
        return self.run_events_to_ledger((_continuous_record_to_event(item) for item in series), strategy, ledger=ledger, run_id=run_id, price_field=price_field, chunk_size=chunk_size)

    def run_events_to_ledger(self, events: Iterable[HistoricalRecord], strategy: EventStrategy, *, ledger, run_id: str, price_field: str = "price", chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        result = self.run_events(events, strategy, price_field=price_field)
        for offset in range(0, len(result.trades), chunk_size):
            ledger.append(run_id, ledger.next_sequence(run_id), result.trades[offset:offset + chunk_size])
        return BacktestResult(result.initial_capital, result.final_capital, result.net_pnl, result.total_return, (), result.win_rate, result.expectancy, result.sharpe_ratio, result.sortino_ratio, result.max_drawdown, result.cagr)

    def run_continuous_futures_events(self, windows: Iterable[FNORolloverWindow], records_by_token: Mapping[str, Iterable[HistoricalRecord]], strategy: EventStrategy, *, price_field: str = "close") -> BacktestResult:
        series = build_continuous_futures_series(windows, records_by_token)
        return self.run_events((_continuous_record_to_event(item) for item in series), strategy, price_field=price_field)

    def run_incremental_to_ledger(self, candles: Iterable[Mapping[str, object]], entry_strategy: Strategy, exit_strategy: Strategy, *, ledger, run_id: str, chunk_size: int = 500) -> BacktestResult:
        if not run_id.strip():
            raise ValueError("run_id is required")
        return self.run_incremental(candles, entry_strategy, exit_strategy, persist_chunk=lambda trades, sequence: ledger.append(run_id, ledger.next_sequence(run_id), trades), chunk_size=chunk_size)

    def run_incremental(self, candles: Iterable[Mapping[str, object]], entry_strategy: Strategy, exit_strategy: Strategy, *, persist_chunk: Callable[[Sequence[BacktestTrade], int], object], chunk_size: int = 500) -> BacktestResult:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        result = self.run(candles, entry_strategy, exit_strategy)
        for offset in range(0, len(result.trades), chunk_size):
            persist_chunk(result.trades[offset:offset + chunk_size], offset // chunk_size)
        return BacktestResult(result.initial_capital, result.final_capital, result.net_pnl, result.total_return, (), result.win_rate, result.expectancy, result.sharpe_ratio, result.sortino_ratio, result.max_drawdown, result.cagr)


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
        return EventSignal(decision.action.upper(), decision.price)
    if isinstance(decision, str):
        return EventSignal(decision.upper())
    raise TypeError("event strategy must return EventSignal, action string, or None")


def _build_trade(config: BacktestConfig, entry_timestamp: object, entry_price: float, exit_timestamp: object, exit_price: float) -> BacktestTrade:
    gross_pnl = (exit_price - entry_price) * config.quantity
    traded_value = (entry_price + exit_price) * config.quantity
    costs = traded_value * config.transaction_cost_rate
    return BacktestTrade(entry_timestamp, exit_timestamp, entry_price, exit_price, config.quantity, gross_pnl, costs, gross_pnl - costs)


def _build_result(initial_capital: float, final_capital: float, trades: list[BacktestTrade], max_drawdown: float) -> BacktestResult:
    wins = sum(1 for trade in trades if trade.net_pnl > 0)
    net_pnl = final_capital - initial_capital
    return BacktestResult(initial_capital, final_capital, net_pnl, net_pnl / initial_capital, tuple(trades), wins / len(trades) if trades else 0.0, net_pnl / len(trades) if trades else 0.0, _trade_sharpe_ratio(trades, initial_capital), _trade_sortino_ratio(trades, initial_capital), max_drawdown, _calculate_cagr(trades, initial_capital, final_capital))


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


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
