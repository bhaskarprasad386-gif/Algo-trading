"""Deterministic Cash-Future convergence backtest."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable
import math

from app.scanner.cash_future_history import CashFutureHistoryPoint, _is_expired


@dataclass(frozen=True)
class BacktestConfig:
    min_entry_gap: float = 0.0
    exit_gap: float = 0.0
    charges_per_trade: float = 0.0
    funding_cost_per_trade: float = 0.0
    max_holding_days: int = 30
    contract_month: str | None = None
    execution_model: str = "gap"

    def __post_init__(self) -> None:
        for value, name in (
            (self.min_entry_gap, "min_entry_gap"),
            (self.exit_gap, "exit_gap"),
            (self.charges_per_trade, "charges_per_trade"),
            (self.funding_cost_per_trade, "funding_cost_per_trade"),
        ):
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.charges_per_trade < 0 or self.funding_cost_per_trade < 0:
            raise ValueError(
                "charges_per_trade and funding_cost_per_trade must be non-negative"
            )
        if (
            not isinstance(self.max_holding_days, int)
            or isinstance(self.max_holding_days, bool)
            or self.max_holding_days <= 0
        ):
            raise ValueError("max_holding_days must be a positive integer")
        if self.execution_model not in {"gap", "bid_ask"}:
            raise ValueError("execution_model must be 'gap' or 'bid_ask'")


def _executable_spread_profit(
    entry: CashFutureHistoryPoint, exit_point: CashFutureHistoryPoint
) -> float:
    prices = (
        entry.cash_ask,
        entry.future_bid,
        exit_point.cash_bid,
        exit_point.future_ask,
    )
    if any(price is None or float(price) <= 0 for price in prices):
        raise ValueError(
            "bid_ask execution requires entry cash_ask/future_bid and "
            "exit cash_bid/future_ask"
        )
    return (
        (float(exit_point.cash_bid) - float(entry.cash_ask))
        + (float(entry.future_bid) - float(exit_point.future_ask))
    ) * entry.lot_size


def _legacy_gap_profit(
    entry: CashFutureHistoryPoint, exit_point: CashFutureHistoryPoint
) -> float:
    return (entry.gap - exit_point.gap) * entry.lot_size


class CashFutureBacktestProcessor:
    """Incremental one-point state machine for one symbol/contract series.

    The default collectors preserve the legacy run_backtest result contract.
    The state itself does not require the historical input series.
    """

    def __init__(
        self,
        config: BacktestConfig,
        cancel_check: Callable[[], bool] | None = None,
        *,
        retain_outputs: bool = True,
    ) -> None:
        self.config = config
        self.cancel_check = cancel_check
        self.retain_outputs = retain_outputs

        self.entry: CashFutureHistoryPoint | None = None
        self.equity = 0.0
        self.peak = 0.0
        self.max_drawdown = 0.0
        self.total_capital = 0.0
        self.seen_contract: str | None = None
        self.seen_symbol: str | None = None
        self.previous_timestamp = None

        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self.wins = 0
        self.losses = 0
        self.trade_count = 0

    def _append_equity(self, point: CashFutureHistoryPoint) -> None:
        if self.retain_outputs:
            self.equity_curve.append(
                {"timestamp": point.timestamp.isoformat(), "equity": self.equity}
            )

    def _validate_point(self, point: CashFutureHistoryPoint) -> None:
        if (
            self.previous_timestamp is not None
            and point.timestamp < self.previous_timestamp
        ):
            raise ValueError("backtest input must be ordered by timestamp")
        self.previous_timestamp = point.timestamp

        if self.seen_contract is None:
            self.seen_contract = point.contract_month
        elif point.contract_month != self.seen_contract:
            raise ValueError(
                "backtest input contains multiple contract months; "
                "run each contract separately"
            )

        if self.seen_symbol is None:
            self.seen_symbol = point.symbol
        elif point.symbol != self.seen_symbol:
            raise ValueError(
                "backtest input contains multiple symbols; run each symbol separately"
            )

    def _completed_trade(
        self, point: CashFutureHistoryPoint
    ) -> dict:
        assert self.entry is not None
        entry = self.entry
        gross = (
            _legacy_gap_profit(entry, point)
            if self.config.execution_model == "gap"
            else _executable_spread_profit(entry, point)
        )
        net = (
            gross
            - self.config.charges_per_trade
            - self.config.funding_cost_per_trade
        )
        capital = entry.cash_price * entry.lot_size + entry.margin_required
        roi = net / capital * 100.0 if capital else 0.0
        converged = point.gap <= self.config.exit_gap
        expired = _is_expired(point.timestamp, entry.expiry_date)
        holding_days = (
            point.timestamp - entry.timestamp
        ).total_seconds() / 86400.0
        timed_out = holding_days >= self.config.max_holding_days
        reason = (
            "convergence"
            if converged
            else ("expiry" if expired else "max_holding")
        )
        return {
            "entry_time": entry.timestamp.isoformat(),
            "exit_time": point.timestamp.isoformat(),
            "entry_gap": entry.gap,
            "exit_gap": point.gap,
            "lot_size": entry.lot_size,
            "filled_quantity": entry.lot_size,
            "gross_profit": gross,
            "charges": self.config.charges_per_trade,
            "funding_cost": self.config.funding_cost_per_trade,
            "net_profit": net,
            "roi_pct": roi,
            "exit_reason": reason,
            "execution_model": self.config.execution_model,
            "symbol": entry.symbol,
            "contract_month": entry.contract_month,
        }

    def process(self, point: CashFutureHistoryPoint) -> dict | None:
        """Consume one point and return a completed trade, if any."""
        if (
            self.config.contract_month is not None
            and point.contract_month != self.config.contract_month
        ):
            return None

        self._validate_point(point)

        if self.entry is None:
            if not _is_expired(point.timestamp, point.expiry_date) and (
                point.gap >= self.config.min_entry_gap
            ):
                self.entry = point
            self._append_equity(point)
            return None

        holding_days = (
            point.timestamp - self.entry.timestamp
        ).total_seconds() / 86400.0
        converged = point.gap <= self.config.exit_gap
        expired = _is_expired(point.timestamp, self.entry.expiry_date)
        timed_out = holding_days >= self.config.max_holding_days

        if not (converged or expired or timed_out):
            self._append_equity(point)
            return None

        trade = self._completed_trade(point)
        self.trade_count += 1
        if trade["net_profit"] > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.equity += trade["net_profit"]
        self.total_capital += (
            self.entry.cash_price * self.entry.lot_size
            + self.entry.margin_required
        )
        self.peak = max(self.peak, self.equity)
        self.max_drawdown = max(
            self.max_drawdown, self.peak - self.equity
        )
        if self.retain_outputs:
            self.trades.append(trade)
        self._append_equity(point)
        self.entry = None
        return trade

    def cancelled_result(self) -> dict:
        return self._result(status="cancelled")

    def finalize(self) -> dict:
        return self._result()

    def _open_position(self) -> dict | None:
        if self.entry is None:
            return None
        return {
            "entry_time": self.entry.timestamp.isoformat(),
            "symbol": self.entry.symbol,
            "contract_month": self.entry.contract_month,
            "entry_gap": self.entry.gap,
            "lot_size": self.entry.lot_size,
        }

    def _result(self, *, status: str | None = None) -> dict:
        result = {
            "trade_count": self.trade_count,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": (
                self.wins / self.trade_count * 100.0
                if self.trade_count
                else 0.0
            ),
            "net_profit": self.equity,
            "roi_pct": (
                self.equity / self.total_capital * 100.0
                if self.total_capital
                else 0.0
            ),
            "invested_capital": self.total_capital,
            "max_drawdown": self.max_drawdown,
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "open_position": self._open_position(),
        }
        if status is not None:
            result["status"] = status
        return result


def run_backtest(
    points: Iterable[CashFutureHistoryPoint],
    config: BacktestConfig,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    processor = CashFutureBacktestProcessor(config, cancel_check)
    iterator = iter(points)
    while True:
        if cancel_check is not None and cancel_check():
            return processor.cancelled_result()
        try:
            point = next(iterator)
        except StopIteration:
            return processor.finalize()
        processor.process(point)


def _aggregate_contract_results(results: list[dict]) -> dict:
    trades = [
        trade for result in results for trade in result["trades"]
    ]
    trades.sort(
        key=lambda trade: (
            trade["entry_time"],
            trade.get("symbol", ""),
            trade.get("contract_month", ""),
        )
    )
    wins = sum(1 for trade in trades if trade["net_profit"] > 0)
    net_profit = sum(trade["net_profit"] for trade in trades)
    invested_capital = sum(
        result["invested_capital"] for result in results
    )
    equity = 0.0
    running_peak = 0.0
    max_drawdown = 0.0
    equity_curve = []
    if trades:
        equity_curve.append(
            {"timestamp": trades[0]["entry_time"], "equity": 0.0}
        )
    for trade in trades:
        equity += trade["net_profit"]
        running_peak = max(running_peak, equity)
        max_drawdown = max(
            max_drawdown, running_peak - equity
        )
        equity_curve.append(
            {"timestamp": trade["exit_time"], "equity": equity}
        )
    open_positions = [
        result["open_position"]
        for result in results
        if result.get("open_position") is not None
    ]
    contract_keys = {
        (position.get("symbol"), position.get("contract_month"))
        for result in results
        if (position := result.get("open_position")) is not None
    }
    contract_keys.update(
        (trade.get("symbol"), trade.get("contract_month"))
        for trade in trades
        if trade.get("contract_month") is not None
    )
    return {
        "contract_count": len(contract_keys) if contract_keys else len(results),
        "trade_count": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "net_profit": net_profit,
        "roi_pct": (
            net_profit / invested_capital * 100.0
            if invested_capital
            else 0.0
        ),
        "invested_capital": invested_capital,
        "max_drawdown": max_drawdown,
        "equity_curve": equity_curve,
        "trades": trades,
        "open_positions": open_positions,
        "per_contract": results,
    }


def _aggregate_contract_results_from_iterable(
    results: list[dict],
    trades_iter: Iterable[dict],
) -> dict:
    """Aggregate compact contract state from a chronological trade stream."""
    wins = 0
    trade_count = 0
    net_profit = 0.0
    invested_capital = sum(result["invested_capital"] for result in results)
    equity = 0.0
    running_peak = 0.0
    max_drawdown = 0.0
    equity_curve: list[dict] = []
    trades: list[dict] = []
    first_entry_time = None

    for trade in trades_iter:
        if first_entry_time is None:
            first_entry_time = trade["entry_time"]
            equity_curve.append({"timestamp": first_entry_time, "equity": 0.0})
        trades.append(trade)
        trade_count += 1
        if trade["net_profit"] > 0:
            wins += 1
        net_profit += trade["net_profit"]
        equity += trade["net_profit"]
        running_peak = max(running_peak, equity)
        max_drawdown = max(max_drawdown, running_peak - equity)
        equity_curve.append({"timestamp": trade["exit_time"], "equity": equity})

    open_positions = [
        result["open_position"]
        for result in results
        if result.get("open_position") is not None
    ]
    contract_keys = {
        (position.get("symbol"), position.get("contract_month"))
        for position in open_positions
    }
    contract_keys.update(
        (trade.get("symbol"), trade.get("contract_month"))
        for trade in trades
        if trade.get("contract_month") is not None
    )
    contract_count = len(contract_keys) if contract_keys else len(results)
    return {
        "contract_count": contract_count,
        "trade_count": trade_count,
        "wins": wins,
        "losses": trade_count - wins,
        "win_rate_pct": wins / trade_count * 100.0 if trade_count else 0.0,
        "net_profit": net_profit,
        "roi_pct": net_profit / invested_capital * 100.0 if invested_capital else 0.0,
        "invested_capital": invested_capital,
        "max_drawdown": max_drawdown,
        "equity_curve": equity_curve,
        "trades": trades,
        "open_positions": open_positions,
        "per_contract": results,
    }


def run_multi_contract_backtest(
    points: Iterable[CashFutureHistoryPoint],
    config: BacktestConfig,
) -> dict:
    grouped: dict[
        tuple[str, str], list[CashFutureHistoryPoint]
    ] = {}
    for point in points:
        if (
            config.contract_month is not None
            and point.contract_month != config.contract_month
        ):
            continue
        grouped.setdefault(
            (point.contract_month, point.symbol), []
        ).append(point)
    results = [
        run_backtest(grouped[key], config)
        for key in sorted(grouped)
    ]
    return _aggregate_contract_results(results)


def run_multi_contract_backtest_streaming(
    points: Iterable[CashFutureHistoryPoint],
    config: BacktestConfig,
    *,
    trade_sink: Callable[[Iterable[dict]], int] | None = None,
    trade_source: Callable[[], Iterable[dict]] | None = None,
    trade_batch_size: int = 500,
) -> dict:
    """Process a symbol/contract ordered stream with optional bounded trade persistence."""
    if trade_batch_size <= 0:
        raise ValueError("trade_batch_size must be positive")
    if trade_source is not None and trade_sink is None:
        raise ValueError("trade_source requires trade_sink")

    results = []
    current_key = None
    processor: CashFutureBacktestProcessor | None = None
    previous_timestamp = None
    seen_keys = set()
    pending_trades: list[dict] = []

    def flush_trades() -> None:
        if trade_sink is not None and pending_trades:
            trade_sink(tuple(pending_trades))
            pending_trades.clear()

    for point in points:
        if (
            config.contract_month is not None
            and point.contract_month != config.contract_month
        ):
            continue

        key = (point.contract_month, point.symbol)
        if current_key is None:
            current_key = key
            processor = CashFutureBacktestProcessor(
                config, retain_outputs=trade_sink is None
            )
        elif key != current_key:
            assert processor is not None
            results.append(processor.finalize())
            flush_trades()
            seen_keys.add(current_key)
            if key in seen_keys:
                raise ValueError(
                    "streaming backtest input must keep each "
                    "symbol/contract series contiguous"
                )
            current_key = key
            processor = CashFutureBacktestProcessor(
                config, retain_outputs=trade_sink is None
            )
            previous_timestamp = None

        if (
            previous_timestamp is not None
            and point.timestamp < previous_timestamp
        ):
            raise ValueError(
                "streaming backtest input must be ordered by timestamp "
                "within each symbol/contract"
            )
        previous_timestamp = point.timestamp
        assert processor is not None
        trade = processor.process(point)
        if trade_sink is not None and trade is not None:
            pending_trades.append(trade)
            if len(pending_trades) >= trade_batch_size:
                flush_trades()

    if processor is not None:
        results.append(processor.finalize())
    flush_trades()

    if trade_sink is None:
        return _aggregate_contract_results(results)

    # The durable sink owns the trade history; reconstruct the public aggregate
    # through the sink consumer rather than retaining all trades during replay.
    assert trade_source is not None
    return _aggregate_contract_results_from_iterable(results, trade_source())


__all__ = [
    "BacktestConfig",
    "CashFutureBacktestProcessor",
    "run_backtest",
    "run_multi_contract_backtest",
    "run_multi_contract_backtest_streaming",
]
