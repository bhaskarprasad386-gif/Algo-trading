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
    """Immutable context delivered to a high-resolution event strategy."""

    timestamp_ns: int
    sequence: int | None
    source: str
    instrument: str
    payload: Mapping[str, object]
    record: HistoricalRecord


@dataclass(frozen=True)
class EventSignal:
    """Minimal event strategy decision; price is optional when supplied by the event."""

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

    def run(
        self,
        candles: Iterable[Mapping[str, object]],
        entry_strategy: Strategy,
        exit_strategy: Strategy,
    ) -> BacktestResult:
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
                entry_price = close * (1.0 + self.config.slippage_rate)
                open_trade = (timestamp, entry_price)
            elif open_trade is not None and exit_strategy.evaluate(context):
                entry_timestamp, entry_price = open_trade
                exit_price = close * (1.0 - self.config.slippage_rate)
                trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                capital += trade.net_pnl
                peak_capital = max(peak_capital, capital)
                drawdown = (peak_capital - capital) / peak_capital
                max_drawdown = max(max_drawdown, drawdown)
                trades.append(trade)
                open_trade = None

        return _build_result(self.config.initial_capital, capital, trades, max_drawdown)

    def run_events(
        self,
        events: Iterable[HistoricalRecord],
        strategy: EventStrategy,
        *,
        price_field: str = "price",
    ) -> BacktestResult:
        """Run an arbitrary strategy directly on non-cadenced historical events."""
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
            signal = _normalize_event_signal(decision)
            if signal.action in {"HOLD", "NONE"}:
                continue

            price = signal.price
            if price is None:
                raw_price = record.payload.get(price_field)
                if not _is_number(raw_price):
                    raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
                price = float(raw_price)
            if price <= 0:
                raise ValueError("event execution price must be positive")

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

    def run_catalog_events(
        self,
        catalog: HistoricalCatalog,
        *,
        source: str,
        instrument: str,
        strategy: EventStrategy,
        timeframe: str = "tick",
        start_ns: int | None = None,
        end_ns: int | None = None,
        price_field: str = "price",
    ) -> BacktestResult:
        """Backtest cataloged events without materializing or transforming bars."""
        events = catalog.events(
            source=source,
            instrument=instrument,
            timeframe=timeframe,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        return self.run_events(events, strategy, price_field=price_field)

    def run_continuous_futures(
        self,
        windows: Iterable[FNORolloverWindow],
        records_by_token: Mapping[str, Iterable[HistoricalRecord]],
        entry_strategy: Strategy,
        exit_strategy: Strategy,
    ) -> BacktestResult:
        """Backtest an expiry-driven continuous futures view over raw history."""
        series = build_continuous_futures_series(windows, records_by_token)
        candles = (_continuous_record_to_candle(item) for item in series)
        return self.run(candles, entry_strategy, exit_strategy)

    def run_continuous_futures_events(
        self,
        windows: Iterable[FNORolloverWindow],
        records_by_token: Mapping[str, Iterable[HistoricalRecord]],
        strategy: EventStrategy,
        *,
        price_field: str = "close",
    ) -> BacktestResult:
        """Replay continuous futures through the event strategy path, preserving contract identity."""
        series = build_continuous_futures_series(windows, records_by_token)
        events = (_continuous_record_to_event(item) for item in series)
        return self.run_events(events, strategy, price_field=price_field)

    def run_incremental(
        self,
        candles: Iterable[Mapping[str, object]],
        entry_strategy: Strategy,
        exit_strategy: Strategy,
        *,
        persist_chunk: Callable[[Sequence[BacktestTrade], int], object],
        chunk_size: int = 500,
    ) -> BacktestResult:
        """Run without retaining the complete trade ledger in memory."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        capital = self.config.initial_capital
        peak_capital = capital
        max_drawdown = 0.0
        open_trade: tuple[object, float] | None = None
        chunk: list[BacktestTrade] = []
        sequence = 0
        trade_count = 0
        wins = 0
        net_pnl_sum = 0.0
        return_sum = 0.0
        return_square_sum = 0.0
        downside_square_sum = 0.0
        first_entry: object | None = None
        last_exit: object | None = None

        for candle in candles:
            timestamp = candle.get("timestamp")
            close = float(candle["close"])
            if close <= 0:
                raise ValueError("candle close must be positive")
            context = {key: float(value) for key, value in candle.items() if _is_number(value)}

            if open_trade is None and entry_strategy.evaluate(context):
                entry_price = close * (1.0 + self.config.slippage_rate)
                open_trade = (timestamp, entry_price)
            elif open_trade is not None and exit_strategy.evaluate(context):
                entry_timestamp, entry_price = open_trade
                exit_price = close * (1.0 - self.config.slippage_rate)
                trade = _build_trade(self.config, entry_timestamp, entry_price, timestamp, exit_price)
                capital += trade.net_pnl
                peak_capital = max(peak_capital, capital)
                max_drawdown = max(max_drawdown, (peak_capital - capital) / peak_capital)
                trade_count += 1
                wins += int(trade.net_pnl > 0)
                net_pnl_sum += trade.net_pnl
                trade_return = trade.net_pnl / self.config.initial_capital
                return_sum += trade_return
                return_square_sum += trade_return * trade_return
                downside_square_sum += min(trade_return, 0.0) ** 2
                first_entry = trade.entry_timestamp if first_entry is None else first_entry
                last_exit = trade.exit_timestamp
                chunk.append(trade)
                if len(chunk) >= chunk_size:
                    persist_chunk(tuple(chunk), sequence)
                    sequence += 1
                    chunk.clear()
                open_trade = None

        if chunk:
            persist_chunk(tuple(chunk), sequence)

        win_rate = wins / trade_count if trade_count else 0.0
        expectancy = net_pnl_sum / trade_count if trade_count else 0.0
        sharpe_ratio = _ratio_from_moments(return_sum, return_square_sum, trade_count)
        sortino_ratio = ((return_sum / trade_count) / sqrt(downside_square_sum / trade_count) if trade_count and downside_square_sum > 0 else 0.0)
        cagr = _calculate_cagr_from_timestamps(first_entry, last_exit, self.config.initial_capital, capital)
        return BacktestResult(
            initial_capital=self.config.initial_capital,
            final_capital=capital,
            net_pnl=capital - self.config.initial_capital,
            total_return=(capital - self.config.initial_capital) / self.config.initial_capital,
            trades=(),
            win_rate=win_rate,
            expectancy=expectancy,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            cagr=cagr,
        )


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
    return HistoricalRecord(
        source=item.record.source,
        instrument=item.record.instrument,
        timeframe=item.record.timeframe,
        timestamp_ns=item.timestamp_ns,
        payload=payload,
        sequence=item.record.sequence,
    )


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


def _ratio_from_moments(return_sum: float, return_square_sum: float, count: int) -> float:
    if count < 2:
        return 0.0
    mean_return = return_sum / count
    variance = max(return_square_sum / count - mean_return * mean_return, 0.0)
    return mean_return / sqrt(variance) if variance > 0 else 0.0


def _calculate_cagr_from_timestamps(start: object | None, end: object | None, initial_capital: float, final_capital: float) -> float:
    if start is None or end is None or final_capital <= 0:
        return 0.0
    if not isinstance(start, (datetime, date)) or not isinstance(end, (datetime, date)):
        return 0.0
    if isinstance(start, datetime) != isinstance(end, datetime):
        return 0.0
    years = (end - start).total_seconds() / (365.25 * 24 * 60 * 60) if isinstance(start, datetime) else (end - start).days / 365.25
    return (final_capital / initial_capital) ** (1.0 / years) - 1.0 if years > 0 else 0.0


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
    downside_deviation = sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    return mean_return / downside_deviation if downside_deviation else 0.0


def _calculate_cagr(trades: list[BacktestTrade], initial_capital: float, final_capital: float) -> float:
    if not trades or final_capital <= 0:
        return 0.0
    return _calculate_cagr_from_timestamps(trades[0].entry_timestamp, trades[-1].exit_timestamp, initial_capital, final_capital)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
