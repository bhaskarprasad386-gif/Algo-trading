"""Historical Cash-Future strategy application and deterministic execution."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Iterator, Mapping, Any
import json

from app.backtesting.cash_future_strategy_checkpoint import CashFutureStrategyCheckpoint
from app.scanner.cash_future_backtest import _executable_spread_profit, _legacy_gap_profit
from app.scanner.cash_future_history import CashFutureHistoryPoint


CashFutureStrategy = Callable[[CashFutureHistoryPoint, tuple[CashFutureHistoryPoint, ...]], str | None]


@dataclass(frozen=True)
class CashFutureStrategyConfig:
    initial_capital: float = 100_000_000.0
    execution_model: str = "gap"
    charges_per_trade: float = 0.0
    funding_cost_per_trade: float = 0.0
    start_date: date | None = None
    end_date: date | None = None
    contract_month: str | None = None
    history_window: int | None = None
    checkpoint_interval: int | None = None

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.execution_model not in {"gap", "bid_ask"}:
            raise ValueError("execution_model must be 'gap' or 'bid_ask'")
        if self.start_date is not None and self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if self.history_window is not None and self.history_window <= 0:
            raise ValueError("history_window must be positive when provided")
        if self.checkpoint_interval is not None and self.checkpoint_interval <= 0:
            raise ValueError("checkpoint_interval must be positive when provided")


@dataclass
class CashFutureCapitalLedger:
    """Track realized capital separately from margin reserved by open spreads."""

    initial_capital: float
    realized_capital: float | None = None
    reserved_margin: float = 0.0
    blocked_entries: int = 0

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.realized_capital is None:
            self.realized_capital = self.initial_capital

    @property
    def available_capital(self) -> float:
        return float(self.realized_capital) - self.reserved_margin

    def reserve(self, margin_required: float) -> bool:
        margin = max(float(margin_required), 0.0)
        if margin > self.available_capital:
            self.blocked_entries += 1
            return False
        self.reserved_margin += margin
        return True

    def release(self, margin_required: float) -> None:
        margin = max(float(margin_required), 0.0)
        self.reserved_margin -= margin
        if self.reserved_margin < 0:
            raise ValueError("reserved margin cannot become negative")

    def apply_realized_pnl(self, net_profit: float) -> None:
        self.realized_capital = float(self.realized_capital) + float(net_profit)


class _LedgerPayloadSequence(Sequence[Mapping[str, Any]]):
    """Lazy result view backed by durable ledger records."""

    def __init__(self, ledger, run_id: str, record_type: str) -> None:
        self._ledger = ledger
        self._run_id = run_id
        self._record_type = record_type

    def __len__(self) -> int:
        return self._ledger.record_count(self._run_id, self._record_type)

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        for record in self._ledger.iter_records(self._run_id, self._record_type):
            yield record.payload

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return tuple(self[i] for i in range(start, stop, step))
        return self._ledger.record_at(self._run_id, self._record_type, index).payload


@dataclass(frozen=True)
class CashFutureStrategyRun:
    strategy_id: str
    strategy_version: str
    initial_capital: float
    final_capital: float
    net_profit: float
    signals: Sequence[Mapping[str, Any]]
    trades: Sequence[Mapping[str, Any]]
    equity_curve: Sequence[Mapping[str, Any]]
    final_available_capital: float = 0.0
    final_reserved_margin: float = 0.0
    blocked_entry_count: int = 0


def run_cash_future_strategy(
    points: Iterable[CashFutureHistoryPoint],
    strategy: CashFutureStrategy,
    *,
    strategy_id: str,
    strategy_version: str = "1",
    config: CashFutureStrategyConfig | None = None,
    ledger=None,
    run_id: str | None = None,
    strategy_hash: str | None = None,
    data_source_fingerprint: str | None = None,
) -> CashFutureStrategyRun:
    """Apply a Cash-Future strategy strictly point-in-time.

    Historical input is consumed incrementally. When a durable ledger is supplied,
    signals, trades and equity are appended directly to SQLite and the returned
    result exposes lazy durable views instead of retaining the full result set in RAM.
    Strategy history is full by default for compatibility. ``history_window`` can
    bound the in-memory prior-observation window for finite-lookback strategies.
    ``checkpoint_interval`` persists runner-owned execution state and, when the
    strategy explicitly implements ``checkpoint_state``, its JSON-safe state.
    Arbitrary strategy internals are deliberately not serialized.
    """
    if not strategy_id.strip():
        raise ValueError("strategy_id is required")
    if not strategy_version.strip():
        raise ValueError("strategy_version is required")
    config = config or CashFutureStrategyConfig()
    point_iter = iter(points)
    previous_timestamp: datetime | date | None = None
    selected_contract: str | None = config.contract_month
    event_index = 0

    if ledger is not None:
        if not run_id or not run_id.strip():
            raise ValueError("run_id is required when ledger persistence is enabled")
        ledger.start_run(
            run_id,
            strategy_id,
            strategy_version,
            config.initial_capital,
            strategy_hash=strategy_hash,
            data_source_fingerprint=data_source_fingerprint,
            metadata={
                "domain": "cash_future",
                "execution_model": config.execution_model,
                "start_date": config.start_date.isoformat() if config.start_date else None,
                "end_date": config.end_date.isoformat() if config.end_date else None,
                "contract_month": config.contract_month,
                "history_window": config.history_window,
                "checkpoint_interval": config.checkpoint_interval,
            },
        )

    if config.history_window is None:
        history: list[CashFutureHistoryPoint] | deque[CashFutureHistoryPoint] = []
    else:
        history = deque(maxlen=config.history_window)
    signals: list[Mapping[str, Any]] | None = [] if ledger is None else None
    trades: list[Mapping[str, Any]] | None = [] if ledger is None else None
    equity_curve: list[Mapping[str, Any]] | None = [] if ledger is None else None
    entry: CashFutureHistoryPoint | None = None
    capital_ledger = CashFutureCapitalLedger(config.initial_capital)
    start_date = config.start_date
    last_point: CashFutureHistoryPoint | None = None

    for point in point_iter:
        point_date = _point_date(point)
        if previous_timestamp is not None and point.timestamp < previous_timestamp:
            raise ValueError("Cash-Future strategy input must be ordered by timestamp")
        previous_timestamp = point.timestamp

        if config.end_date is not None and point_date > config.end_date:
            break
        if selected_contract is None:
            selected_contract = point.contract_month
        if point.contract_month != selected_contract:
            continue

        event_index += 1
        last_point = point
        history.append(point)
        visible_history = tuple(history)
        raw_signal = strategy(point, visible_history)

        if start_date is not None and point_date < start_date:
            _maybe_checkpoint(
                ledger, config, run_id, event_index, point, selected_contract,
                capital_ledger, entry, strategy, strategy_id, strategy_version,
                strategy_hash, data_source_fingerprint,
            )
            continue

        action = "NONE" if raw_signal is None else str(raw_signal).upper()
        if action not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("Cash-Future strategy must return BUY, SELL, HOLD, or NONE")

        signal_record: dict[str, Any] = {
            "timestamp": point.timestamp.isoformat(),
            "symbol": point.symbol,
            "contract_month": point.contract_month,
            "action": action,
            "cash_price": point.cash_price,
            "future_price": point.future_price,
            "gap": point.gap,
            "lot_size": point.lot_size,
        }

        expiry_day = point.expiry_date is not None and point_date >= point.expiry_date
        if action == "BUY" and entry is None:
            if expiry_day:
                signal_record.update(
                    {
                        "execution_status": "blocked",
                        "blocked_reason": "expiry_day_new_entry",
                    }
                )
            elif capital_ledger.reserve(point.margin_required):
                entry = point
            else:
                signal_record.update(
                    {
                        "execution_status": "blocked",
                        "blocked_reason": "insufficient_available_capital",
                        "required_margin": max(float(point.margin_required), 0.0),
                        "available_capital": capital_ledger.available_capital,
                    }
                )
        exit_reason: str | None = None
        if action == "SELL" and entry is not None:
            exit_reason = "strategy"
        elif entry is not None and expiry_day:
            exit_reason = "expiry"

        if ledger is None:
            signals.append(signal_record)
        else:
            ledger.append_batch((ledger_record(run_id, "signal", point.timestamp, signal_record),))

        if exit_reason is not None and entry is not None:
            gross = (
                _legacy_gap_profit(entry, point)
                if config.execution_model == "gap"
                else _executable_spread_profit(entry, point)
            )
            net = gross - config.charges_per_trade - config.funding_cost_per_trade
            capital_ledger.apply_realized_pnl(net)
            capital_ledger.release(entry.margin_required)
            trade = {
                "entry_time": entry.timestamp.isoformat(),
                "exit_time": point.timestamp.isoformat(),
                "symbol": entry.symbol,
                "contract_month": entry.contract_month,
                "lot_size": entry.lot_size,
                "gross_profit": gross,
                "charges": config.charges_per_trade,
                "funding_cost": config.funding_cost_per_trade,
                "net_profit": net,
                "execution_model": config.execution_model,
                "exit_reason": exit_reason,
                "reserved_margin": entry.margin_required,
            }
            if ledger is None:
                trades.append(trade)
            else:
                ledger.append_batch((ledger_record(run_id, "trade", point.timestamp, trade),))
            entry = None

        equity_record = {
            "timestamp": point.timestamp.isoformat(),
            "equity": float(capital_ledger.realized_capital),
            "available_capital": capital_ledger.available_capital,
            "reserved_margin": capital_ledger.reserved_margin,
        }
        if ledger is None:
            equity_curve.append(equity_record)
        else:
            ledger.append_batch((ledger_record(run_id, "equity", point.timestamp, equity_record),))

        _maybe_checkpoint(
            ledger, config, run_id, event_index, point, selected_contract,
            capital_ledger, entry, strategy, strategy_id, strategy_version,
            strategy_hash, data_source_fingerprint,
        )

    if ledger is not None and last_point is not None and config.checkpoint_interval is not None:
        _write_checkpoint(
            ledger, run_id, event_index, last_point, selected_contract,
            capital_ledger, entry, strategy, strategy_id, strategy_version,
            strategy_hash, data_source_fingerprint,
        )

    if ledger is not None:
        result_signals: Sequence[Mapping[str, Any]] = _LedgerPayloadSequence(ledger, run_id, "signal")
        result_trades: Sequence[Mapping[str, Any]] = _LedgerPayloadSequence(ledger, run_id, "trade")
        result_equity: Sequence[Mapping[str, Any]] = _LedgerPayloadSequence(ledger, run_id, "equity")
    else:
        result_signals = tuple(signals or ())
        result_trades = tuple(trades or ())
        result_equity = tuple(equity_curve or ())

    return CashFutureStrategyRun(
        strategy_id,
        strategy_version,
        config.initial_capital,
        float(capital_ledger.realized_capital),
        float(capital_ledger.realized_capital) - config.initial_capital,
        result_signals,
        result_trades,
        result_equity,
        capital_ledger.available_capital,
        capital_ledger.reserved_margin,
        capital_ledger.blocked_entries,
    )


def _maybe_checkpoint(
    ledger,
    config: CashFutureStrategyConfig,
    run_id: str | None,
    event_index: int,
    point: CashFutureHistoryPoint,
    selected_contract: str | None,
    capital_ledger: CashFutureCapitalLedger,
    entry: CashFutureHistoryPoint | None,
    strategy,
    strategy_id: str,
    strategy_version: str,
    strategy_hash: str | None,
    data_source_fingerprint: str | None,
) -> None:
    if ledger is None or config.checkpoint_interval is None:
        return
    if event_index % config.checkpoint_interval != 0:
        return
    _write_checkpoint(
        ledger, run_id, event_index, point, selected_contract,
        capital_ledger, entry, strategy, strategy_id, strategy_version,
        strategy_hash, data_source_fingerprint,
    )


def _write_checkpoint(
    ledger,
    run_id: str | None,
    event_index: int,
    point: CashFutureHistoryPoint,
    selected_contract: str | None,
    capital_ledger: CashFutureCapitalLedger,
    entry: CashFutureHistoryPoint | None,
    strategy,
    strategy_id: str,
    strategy_version: str,
    strategy_hash: str | None,
    data_source_fingerprint: str | None,
) -> None:
    if ledger is None or run_id is None:
        return
    checkpoint = CashFutureStrategyCheckpoint(
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=strategy_hash,
        last_timestamp=point.timestamp.isoformat(),
        selected_contract=selected_contract,
        realized_capital=float(capital_ledger.realized_capital),
        reserved_margin=float(capital_ledger.reserved_margin),
        blocked_entries=int(capital_ledger.blocked_entries),
        open_entry=_serialize_entry(entry),
        source_fingerprint=data_source_fingerprint,
        strategy_state=_capture_strategy_state(strategy),
    )
    from app.backtesting.ledger import Checkpoint
    ledger.checkpoint(
        Checkpoint(
            run_id=run_id,
            event_index=event_index,
            timestamp_ns=int(point.timestamp.timestamp() * 1_000_000_000),
            state=json.loads(checkpoint.to_json()),
        )
    )


def _capture_strategy_state(strategy) -> Mapping[str, Any] | None:
    capture = getattr(strategy, "checkpoint_state", None)
    if capture is None:
        return None
    state = capture()
    if not isinstance(state, Mapping):
        raise ValueError("Cash-Future strategy checkpoint_state() must return a Mapping")
    try:
        json.dumps(state)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Cash-Future strategy checkpoint_state() must return JSON-serializable data"
        ) from exc
    return dict(state)


def _serialize_entry(entry: CashFutureHistoryPoint | None) -> Mapping[str, Any] | None:
    if entry is None:
        return None
    return {
        "timestamp": entry.timestamp.isoformat(),
        "symbol": entry.symbol,
        "contract_month": entry.contract_month,
        "cash_price": entry.cash_price,
        "future_price": entry.future_price,
        "gap": entry.gap,
        "gap_pct": entry.gap_pct,
        "lot_size": entry.lot_size,
        "margin_required": entry.margin_required,
        "expiry_date": entry.expiry_date.isoformat() if entry.expiry_date else None,
    }


def ledger_record(run_id: str, record_type: str, timestamp: datetime, payload: Mapping[str, Any]):
    from app.backtesting.ledger import LedgerRecord
    return LedgerRecord(run_id=run_id, record_type=record_type, timestamp_ns=int(timestamp.timestamp() * 1_000_000_000), payload=dict(payload))


def _point_date(point: CashFutureHistoryPoint) -> date:
    return point.timestamp.date()
