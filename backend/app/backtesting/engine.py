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
    contract_multiplier: float = 1.0
    currency: str = "INR"
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
        for name, value in (
            ("initial_capital", self.initial_capital),
            ("quantity", self.quantity),
            ("contract_multiplier", self.contract_multiplier),
            ("slippage_rate", self.slippage_rate),
            ("transaction_cost_rate", self.transaction_cost_rate),
            ("transaction_cost_minimum", self.transaction_cost_minimum),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number")
        if self.initial_capital <= 0 or self.quantity <= 0 or self.contract_multiplier <= 0:
            raise ValueError("initial_capital, quantity, and contract_multiplier must be positive")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("currency must be a non-empty string")
        if self.quantity_step is not None and (
            self.quantity_step <= 0 or not _is_multiple(self.quantity, self.quantity_step)
        ):
            raise ValueError("quantity must be an exact multiple of quantity_step")
        if self.tick_size is not None and self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.slippage_rate < 0 or self.slippage_rate >= 1:
            raise ValueError("slippage_rate must be in [0, 1)")
        for name, value in (("entry_slippage_rate", self.entry_slippage_rate), ("exit_slippage_rate", self.exit_slippage_rate)):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                or value < 0
                or value >= 1
            ):
                raise ValueError(f"{name} must be a finite number in [0, 1)")
        if self.transaction_cost_rate < 0 or self.transaction_cost_minimum < 0:
            raise ValueError("transaction costs cannot be negative")
        if self.execution_timing not in {"close", "next_open"}:
            raise ValueError("invalid execution_timing")

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
    entry_market_price: float | None = None
    exit_market_price: float | None = None
    entry_price_source: str | None = None
    exit_price_source: str | None = None
    entry_event_identity: tuple | None = None
    exit_event_identity: tuple | None = None


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
    liquidation_value: float | None = None


@dataclass(frozen=True)
class EventContext:
    """Read-only event context shared by event strategies.

    The original six fields remain positional/backward-compatible.  The optional
    portfolio/order views are populated by engines that own those boundaries.
    """

    timestamp_ns: int
    sequence: int | None
    source: str
    instrument: str
    payload: Mapping[str, object]
    record: HistoricalRecord
    portfolio_snapshot: object | None = None
    open_orders: tuple[object, ...] = ()
    available_margin: float | None = None


@dataclass(frozen=True)
class EventSignal:
    action: str
    price: float | None = None

    def __post_init__(self):
        if not isinstance(self.action, str) or self.action.strip().upper() not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("action must be BUY, SELL, HOLD, or NONE")
        if self.price is not None and (
            isinstance(self.price, bool)
            or not isinstance(self.price, (int, float))
            or not isfinite(float(self.price))
            or self.price <= 0
        ):
            raise ValueError("signal price must be finite and positive")


EventStrategy = Callable[[EventContext], EventSignal | str | None]


class BacktestEngine:
    def __init__(self, config=None):
        self.config = config or BacktestConfig()

    def run(self, candles, entry_strategy, exit_strategy):
        capital = self.config.initial_capital
        open_trade = None
        trades = []
        curve = []
        previous_timestamp = None
        pending_action = None
        pending_timestamp = None
        last_close = None
        max_intrabar_drawdown = 0.0
        for candle in candles:
            timestamp = _validate_candle(candle, previous_timestamp)
            previous_timestamp = timestamp
            last_close = float(candle["close"])
            context = {k: v for k, v in candle.items() if _is_number(v)}
            peak_before = max((point.equity for point in curve), default=self.config.initial_capital)
            if self.config.execution_timing == "close":
                if open_trade is None and entry_strategy.evaluate(context):
                    execution_price = _execution_price(self.config, last_close, "BUY")
                    capital -= _ensure_cash_available(self.config, capital, execution_price)
                    open_trade = (timestamp, execution_price, last_close)
                elif open_trade is not None and exit_strategy.evaluate(context):
                    et, ep, emp = open_trade
                    xp = _execution_price(self.config, last_close, "SELL")
                    tr = _build_trade(
                        self.config,
                        et,
                        ep,
                        timestamp,
                        xp,
                        entry_market_price=emp,
                        exit_market_price=last_close,
                        entry_price_source="bar:close",
                        exit_price_source="bar:close",
                    )
                    capital += _net_exit_cashflow(self.config, ep, xp)
                    trades.append(tr)
                    open_trade = None
            else:
                if pending_action is not None:
                    if "open" not in candle:
                        raise ValueError("next_open execution requires candle open")
                    op = float(candle["open"])
                    _validate_price_field(op, "open")
                    if pending_action == "BUY" and open_trade is None:
                        xp = _execution_price(self.config, op, "BUY")
                        capital -= _ensure_cash_available(self.config, capital, xp)
                        open_trade = (pending_timestamp, xp, op)
                    elif pending_action == "SELL" and open_trade is not None:
                        et, ep, emp = open_trade
                        xp = _execution_price(self.config, op, "SELL")
                        tr = _build_trade(
                            self.config,
                            et,
                            ep,
                            timestamp,
                            xp,
                            entry_market_price=emp,
                            exit_market_price=op,
                            entry_price_source="bar:open",
                            exit_price_source="bar:open",
                        )
                        capital += _net_exit_cashflow(self.config, ep, xp)
                        trades.append(tr)
                        open_trade = None
                    pending_action = pending_timestamp = None
                signal = (
                    "SELL" if open_trade is not None and exit_strategy.evaluate(context)
                    else "BUY" if open_trade is None and entry_strategy.evaluate(context)
                    else None
                )
                if signal:
                    pending_action, pending_timestamp = signal, timestamp
            if open_trade is not None and "low" in candle:
                _validate_price_field(candle["low"], "low")
                low_equity = capital + _calculate_liquidation_pnl(self.config, open_trade[1], float(candle["low"]))
                if peak_before > 0:
                    max_intrabar_drawdown = max(max_intrabar_drawdown, (peak_before - low_equity) / peak_before)
            curve.append(_equity_point(timestamp, capital, open_trade, last_close, self.config))
        liquidation = _calculate_liquidation_pnl(self.config, open_trade[1], last_close) if open_trade is not None and last_close is not None else 0.0
        return _build_result(
            self.config.initial_capital,
            capital + liquidation,
            trades,
            max_intrabar_drawdown,
            liquidation,
            open_trade is not None,
            curve,
        )

    def run_events(self, events, strategy, *, price_field="price"):
        if not isinstance(price_field, str) or not price_field.strip():
            raise ValueError("price_field is required")
        # Keep event replay streaming: validation and execution happen in one pass.
        # This avoids materializing potentially millions of tick/depth events in RAM.
        seen = set()
        previous_key = None
        capital = self.config.initial_capital
        open_trade = None
        trades = []
        curve = []
        last_price = None
        for record in events:
            if not isinstance(record.timestamp_ns, int) or isinstance(record.timestamp_ns, bool) or record.timestamp_ns < 0:
                raise ValueError("event timestamp_ns must be a non-negative integer")
            if not isinstance(record.instrument, str) or not record.instrument.strip():
                raise ValueError("event instrument is required")
            if not isinstance(record.source, str) or not record.source.strip():
                raise ValueError("event source is required")
            if record.sequence is not None and (
                not isinstance(record.sequence, int) or isinstance(record.sequence, bool) or record.sequence < 0
            ):
                raise ValueError("event sequence must be a non-negative integer or None")
            identity = record.identity()
            if identity in seen:
                raise ValueError("duplicate event identity")
            key = _event_order_key(record)
            if previous_key is not None and key <= previous_key:
                raise ValueError("events must be strictly ordered by timestamp, sequence, and stream identity")
            seen.add(identity)
            previous_key = key
            signal = _normalize_event_signal(
                strategy(EventContext(record.timestamp_ns, record.sequence, record.source, record.instrument, record.payload, record))
            )
            if signal.action in {"HOLD", "NONE"}:
                # HOLD/NONE is still a market event: preserve the event in the
                # equity curve and update the mark when a price is available.
                raw_mark = record.payload.get(price_field)
                if raw_mark is not None:
                    if not _is_number(raw_mark) or float(raw_mark) <= 0:
                        raise ValueError(f"event payload {price_field!r} must be numeric and positive")
                    last_price = float(raw_mark)
                    curve.append(_equity_point(record.timestamp_ns, capital, open_trade, last_price, self.config))
                elif open_trade is not None and last_price is not None:
                    curve.append(_equity_point(record.timestamp_ns, capital, open_trade, last_price, self.config))
                else:
                    curve.append(_equity_point(record.timestamp_ns, capital, None, 0.0, self.config))
                continue
            price, source = _resolve_signal_price(signal, record.payload, price_field)
            last_price = price
            identity = record.identity()
            if open_trade is None and signal.action == "BUY":
                xp = _execution_price(self.config, price, "BUY")
                capital -= _ensure_cash_available(self.config, capital, xp)
                open_trade = (record.timestamp_ns, xp, price, source, identity)
            elif open_trade is not None and signal.action == "SELL":
                et, ep, emp, es, ei = open_trade
                xp = _execution_price(self.config, price, "SELL")
                tr = _build_trade(
                    self.config,
                    et,
                    ep,
                    record.timestamp_ns,
                    xp,
                    entry_market_price=emp,
                    exit_market_price=price,
                    entry_price_source=es,
                    exit_price_source=source,
                    entry_event_identity=ei,
                    exit_event_identity=identity,
                )
                capital += _net_exit_cashflow(self.config, ep, xp)
                trades.append(tr)
                open_trade = None
            curve.append(_equity_point(record.timestamp_ns, capital, open_trade, price, self.config))
        liquidation = _calculate_liquidation_pnl(self.config, open_trade[1], last_price) if open_trade is not None and last_price is not None else 0.0
        return _build_result(
            self.config.initial_capital,
            capital + liquidation,
            trades,
            0.0,
            liquidation,
            open_trade is not None,
            curve,
        )

    def run_catalog_events(self, catalog, *, source, instrument, strategy, timeframe="tick", start_ns=None, end_ns=None, price_field="price"):
        return self.run_events(
            catalog.iter_records(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns),
            strategy,
            price_field=price_field,
        )

    def run_continuous_futures(self, windows, records_by_token, entry_strategy, exit_strategy):
        return self.run(
            (_continuous_record_to_candle(item) for item in build_continuous_futures_series(windows, records_by_token)),
            entry_strategy,
            exit_strategy,
        )

    def run_continuous_futures_events_to_ledger(self, windows, records_by_token, strategy, *, ledger, run_id, price_field="close", chunk_size=500):
        if not run_id.strip():
            raise ValueError("run_id is required")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        from app.backtesting.event_incremental import run_events_incremental
        return run_events_incremental(
            self.config,
            (_continuous_record_to_event(item) for item in build_continuous_futures_series(windows, records_by_token)),
            strategy,
            persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades),
            chunk_size=chunk_size,
            price_field=price_field,
        )

    def run_events_to_ledger(self, events, strategy, *, ledger, run_id, price_field="price", chunk_size=500):
        if not run_id.strip():
            raise ValueError("run_id is required")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        from app.backtesting.event_incremental import run_events_incremental
        return run_events_incremental(
            self.config,
            events,
            strategy,
            persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades),
            chunk_size=chunk_size,
            price_field=price_field,
        )

    def run_continuous_futures_events(self, windows, records_by_token, strategy, *, price_field="close"):
        return self.run_events(
            (_continuous_record_to_event(item) for item in build_continuous_futures_series(windows, records_by_token)),
            strategy,
            price_field=price_field,
        )

    def run_incremental_to_ledger(self, candles, entry_strategy, exit_strategy, *, ledger, run_id, chunk_size=500):
        if not run_id.strip():
            raise ValueError("run_id is required")
        return self.run_incremental(
            candles,
            entry_strategy,
            exit_strategy,
            persist_chunk=lambda trades, sequence: ledger.append(run_id, sequence, trades),
            chunk_size=chunk_size,
        )

    def run_incremental(self, candles, entry_strategy, exit_strategy, *, persist_chunk, chunk_size=500):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        capital = self.config.initial_capital
        open_trade = None
        pending_chunk = []
        trade_count = 0
        win_count = 0
        realized_net_pnl = 0.0
        curve = []
        previous_timestamp = None
        last_close = None
        max_intrabar_drawdown = 0.0
        for candle in candles:
            timestamp = _validate_candle(candle, previous_timestamp)
            previous_timestamp = timestamp
            close = float(candle["close"])
            last_close = close
            context = {k: v for k, v in candle.items() if _is_number(v)}
            peak_before = max((point.equity for point in curve), default=self.config.initial_capital)
            if open_trade is None and entry_strategy.evaluate(context):
                xp = _execution_price(self.config, close, "BUY")
                capital -= _ensure_cash_available(self.config, capital, xp)
                open_trade = (timestamp, xp, close)
            elif open_trade is not None and exit_strategy.evaluate(context):
                et, ep, emp = open_trade
                xp = _execution_price(self.config, close, "SELL")
                tr = _build_trade(
                    self.config,
                    et,
                    ep,
                    timestamp,
                    xp,
                    entry_market_price=emp,
                    exit_market_price=close,
                    entry_price_source="bar:close",
                    exit_price_source="bar:close",
                )
                capital += _net_exit_cashflow(self.config, ep, xp)
                trade_count += 1
                realized_net_pnl += tr.net_pnl
                if tr.net_pnl > 0:
                    win_count += 1
                pending_chunk.append(tr)
                if len(pending_chunk) >= chunk_size:
                    persist_chunk(tuple(pending_chunk), (trade_count - 1) // chunk_size)
                    pending_chunk.clear()
                open_trade = None
            if open_trade is not None and "low" in candle:
                _validate_price_field(candle["low"], "low")
                low_equity = capital + _calculate_liquidation_pnl(self.config, open_trade[1], float(candle["low"]))
                if peak_before > 0:
                    max_intrabar_drawdown = max(max_intrabar_drawdown, (peak_before - low_equity) / peak_before)
            curve.append(_equity_point(timestamp, capital, open_trade, close, self.config))
        if pending_chunk:
            persist_chunk(tuple(pending_chunk), trade_count // chunk_size)
        liquidation = _calculate_liquidation_pnl(self.config, open_trade[1], last_close) if open_trade is not None and last_close is not None else 0.0
        net = capital + liquidation - self.config.initial_capital
        curve_tuple = tuple(curve)
        stats = calculate_statistics(curve_tuple, self.config.initial_capital) if curve_tuple else None
        authoritative_drawdown = max(stats.max_drawdown if stats else 0.0, max_intrabar_drawdown)
        return BacktestResult(
            initial_capital=self.config.initial_capital,
            final_capital=capital + liquidation,
            net_pnl=net,
            total_return=net / self.config.initial_capital,
            trades=(),
            win_rate=win_count / trade_count if trade_count else 0.0,
            expectancy=net / trade_count if trade_count else 0.0,
            sharpe_ratio=stats.sharpe_ratio if stats else None,
            sortino_ratio=stats.sortino_ratio if stats else None,
            max_drawdown=authoritative_drawdown,
            cagr=stats.cagr if stats else None,
            unrealized_pnl=liquidation,
            has_open_trade=open_trade is not None,
            equity_curve=curve_tuple,
            liquidation_value=capital + liquidation,
        )


def _event_order_key(record):
    sequence = record.sequence if record.sequence is not None else -1
    return (record.timestamp_ns, sequence, record.source, record.instrument, record.timeframe)


def _validate_candle(candle, previous_timestamp):
    if not isinstance(candle, Mapping):
        raise ValueError("candle must be a mapping")
    timestamp = candle.get("timestamp")
    if timestamp is None:
        raise ValueError("candle timestamp is required")
    _validate_timestamp_type(timestamp)
    if previous_timestamp is not None:
        _validate_timestamp_type(previous_timestamp)
        if _timestamp_kind(timestamp) != _timestamp_kind(previous_timestamp):
            raise ValueError("candle timestamps must use comparable timestamp types")
        try:
            if timestamp <= previous_timestamp:
                raise ValueError("candles must have strictly increasing timestamps")
        except TypeError as exc:
            raise ValueError("candle timestamps must be comparable") from exc
    if "close" not in candle:
        raise ValueError("candle close is required")
    for field in ("open", "high", "low", "close"):
        if field in candle:
            _validate_price_field(candle[field], field)
    return timestamp


def _timestamp_kind(value):
    if isinstance(value, bool):
        raise ValueError("candle timestamp must be a valid timestamp")
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (int, float)) and isfinite(float(value)):
        return "numeric"
    raise ValueError("candle timestamp must be numeric, date, or datetime")


def _validate_timestamp_type(value):
    _timestamp_kind(value)


def _is_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float, Decimal)) and isfinite(float(value))

def _validate_price_field(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"candle {field} must be finite and positive")


def _execution_price(config, market_price, side):
    slippage = config.effective_entry_slippage if side == "BUY" else config.effective_exit_slippage
    return _round_price(market_price * (1 + slippage if side == "BUY" else 1 - slippage), config.tick_size)


def _round_price(price, tick_size):
    if tick_size is None:
        return price
    return float(
        (Decimal(str(price)) / Decimal(str(tick_size))).quantize(Decimal("1"), rounding="ROUND_HALF_UP")
        * Decimal(str(tick_size))
    )


def _entry_transaction_cost(config, entry_price):
    notional = entry_price * config.quantity * config.contract_multiplier
    return max(config.transaction_cost_minimum, notional * config.transaction_cost_rate)


def _trade_total_cost(config, entry_price, exit_price):
    value = (entry_price + exit_price) * config.quantity * config.contract_multiplier
    return max(config.transaction_cost_minimum, value * config.transaction_cost_rate)


def _exit_transaction_cost(config, entry_price, exit_price):
    total = _trade_total_cost(config, entry_price, exit_price)
    entry_cost = _entry_transaction_cost(config, entry_price)
    return max(0.0, total - entry_cost)


def _ensure_cash_available(config, capital, entry_price):
    notional = entry_price * config.quantity * config.contract_multiplier
    entry_cost = _entry_transaction_cost(config, entry_price)
    if config.enforce_cash and notional + entry_cost > capital:
        raise ValueError("insufficient cash for backtest entry")
    return entry_cost


def _net_exit_cashflow(config, entry_price, exit_price):
    gross = (exit_price - entry_price) * config.quantity * config.contract_multiplier
    return gross - _exit_transaction_cost(config, entry_price, exit_price)


def _is_multiple(value, step):
    ratio = Decimal(str(value)) / Decimal(str(step))
    return ratio == ratio.to_integral_value()


def _timestamp_ns(value):
    if isinstance(value, datetime):
        return int((value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp() * 1e9)
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1e9)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return int(value)
    raise ValueError("invalid timestamp")


def _equity_point(timestamp, capital, open_trade, mark_price, config):
    liquidation_pnl = _calculate_liquidation_pnl(config, open_trade[1], mark_price) if open_trade is not None else 0.0
    return EquityPoint(_timestamp_ns(timestamp), capital + liquidation_pnl, capital - config.initial_capital, liquidation_pnl)


def _continuous_record_to_candle(item):
    candle = dict(item.payload)
    candle["timestamp"] = item.timestamp_ns
    return candle


def _continuous_record_to_event(item):
    payload = dict(item.payload)
    payload["contract_token"] = item.contract_token
    return HistoricalRecord("continuous_futures", item.record.instrument, item.timeframe, item.timestamp_ns, payload, item.record.sequence)


def _normalize_event_signal(decision):
    if decision is None:
        return EventSignal("NONE")
    if isinstance(decision, EventSignal):
        return EventSignal(decision.action.strip().upper(), decision.price)
    if isinstance(decision, str):
        return EventSignal(decision.strip().upper())
    raise TypeError("event strategy must return EventSignal, action string, or None")


def _resolve_signal_price(decision, payload, price_field):
    if decision.price is not None:
        return float(decision.price), "signal"
    raw = payload.get(price_field)
    if not _is_number(raw):
        raise ValueError(f"event payload must contain numeric {price_field!r} or signal price")
    return float(raw), f"payload:{price_field}"


def _build_trade(
    config,
    entry_timestamp,
    entry_price,
    exit_timestamp,
    exit_price,
    *,
    entry_market_price=None,
    exit_market_price=None,
    entry_price_source=None,
    exit_price_source=None,
    entry_event_identity=None,
    exit_event_identity=None,
):
    gross = (exit_price - entry_price) * config.quantity * config.contract_multiplier
    costs = _trade_total_cost(config, entry_price, exit_price)
    values = (entry_price, exit_price, gross, costs, gross - costs)
    if not all(isfinite(float(value)) for value in values):
        raise ValueError("backtest trade values must be finite")
    return BacktestTrade(
        entry_timestamp,
        exit_timestamp,
        entry_price,
        exit_price,
        config.quantity,
        gross,
        costs,
        gross - costs,
        entry_market_price,
        exit_market_price,
        entry_price_source,
        exit_price_source,
        entry_event_identity,
        exit_event_identity,
    )


def _calculate_liquidation_pnl(config, entry_price, mark_price):
    exit_price = _execution_price(config, mark_price, "SELL")
    gross = (exit_price - entry_price) * config.quantity * config.contract_multiplier
    return gross - _exit_transaction_cost(config, entry_price, exit_price)


def _calculate_unrealized_pnl(config, entry_price, mark_price):
    """Backward-compatible alias; open-position equity uses liquidation semantics."""
    return _calculate_liquidation_pnl(config, entry_price, mark_price)


def _build_result(initial_capital, final_capital, trades, max_drawdown=0.0, unrealized_pnl=0.0, has_open_trade=False, equity_curve=()):
    wins = sum(1 for t in trades if t.net_pnl > 0)
    net = final_capital - initial_capital
    curve = tuple(equity_curve)
    stats = calculate_statistics(curve, initial_capital) if curve else None
    authoritative_drawdown = max(stats.max_drawdown if stats else 0.0, max_drawdown)
    return BacktestResult(
        initial_capital=initial_capital,
        final_capital=final_capital,
        net_pnl=net,
        total_return=net / initial_capital,
        trades=tuple(trades),
        win_rate=wins / len(trades) if trades else 0.0,
        expectancy=net / len(trades) if trades else 0.0,
        sharpe_ratio=stats.sharpe_ratio if stats else None,
        sortino_ratio=stats.sortino_ratio if stats else None,
        max_drawdown=authoritative_drawdown,
        cagr=stats.cagr if stats else None,
        unrealized_pnl=unrealized_pnl,
        has_open_trade=has_open_trade,
        equity_curve=curve,
        liquidation_value=final_capital,
    )


def _calculate_cagr(trades, initial_capital, final_capital):
    if not trades:
        return None
    points = [
        EquityPoint(
            _timestamp_ns(t.exit_timestamp),
            initial_capital + sum(x.net_pnl for x in trades[: i + 1]),
            0.0,
            0.0,
        )
        for i, t in enumerate(trades)
    ]
    return calculate_statistics(points, initial_capital).cagr


def _calculate_cagr_from_timestamps(start, end, initial_capital, final_capital):
    if start is None or end is None or initial_capital <= 0 or final_capital <= 0:
        return None
    start_ns = _timestamp_ns(start)