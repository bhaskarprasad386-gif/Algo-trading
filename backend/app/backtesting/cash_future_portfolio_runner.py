"""Portfolio-level Cash-Future strategy execution with simultaneous positions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from app.backtesting.cash_future_portfolio import CashFuturePortfolioLedger, PositionKey
from app.scanner.cash_future_backtest import _executable_spread_profit, _legacy_gap_profit
from app.scanner.cash_future_history import CashFutureHistoryPoint


PortfolioStrategy = Callable[[CashFutureHistoryPoint, tuple[CashFutureHistoryPoint, ...]], str | None]


@dataclass(frozen=True)
class CashFuturePortfolioStrategyRun:
    initial_capital: float
    final_capital: float
    net_profit: float
    signals: tuple[Mapping[str, Any], ...]
    trades: tuple[Mapping[str, Any], ...]
    equity_curve: tuple[Mapping[str, Any], ...]
    final_available_capital: float
    final_reserved_margin: float
    blocked_entry_count: int
    open_position_count: int


def _unrealized_profit(
    entry: CashFutureHistoryPoint,
    current: CashFutureHistoryPoint,
    execution_model: str,
) -> float:
    """Value an open spread at the current observable executable price."""
    return (
        _legacy_gap_profit(entry, current)
        if execution_model == "gap"
        else _executable_spread_profit(entry, current)
    )


def run_cash_future_portfolio_strategy(
    points: Iterable[CashFutureHistoryPoint],
    strategy: PortfolioStrategy,
    *,
    initial_capital: float = 100_000_000.0,
    execution_model: str = "gap",
    charges_per_trade: float = 0.0,
    funding_cost_per_trade: float = 0.0,
) -> CashFuturePortfolioStrategyRun:
    """Run one strategy chronologically while allowing simultaneous positions.

    Each ``(symbol, contract_month)`` gets an independent open-position slot and
    capital reservation. Contract series can therefore coexist without expiry
    prices being mixed between positions. Strategy history remains strictly
    point-in-time and contains no future observations. Equity includes mark-to-
    market unrealized P&L for every currently open position.
    """
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if execution_model not in {"gap", "bid_ask"}:
        raise ValueError("execution_model must be 'gap' or 'bid_ask'")
    ordered = tuple(points)
    if any(current.timestamp < previous.timestamp for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("Cash-Future portfolio input must be ordered by timestamp")

    account = CashFuturePortfolioLedger(initial_capital)
    history: list[CashFutureHistoryPoint] = []
    entries: dict[PositionKey, CashFutureHistoryPoint] = {}
    signals: list[Mapping[str, Any]] = []
    trades: list[Mapping[str, Any]] = []
    equity: list[Mapping[str, Any]] = []

    for point in ordered:
        visible_history = tuple(history + [point])
        raw_signal = strategy(point, visible_history)
        history.append(point)
        action = "NONE" if raw_signal is None else str(raw_signal).upper()
        if action not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("Cash-Future portfolio strategy must return BUY, SELL, HOLD, or NONE")

        key: PositionKey = (point.symbol, point.contract_month)
        entry = entries.get(key)
        signal_record: dict[str, Any] = {
            "timestamp": point.timestamp.isoformat(),
            "symbol": point.symbol,
            "contract_month": point.contract_month,
            "action": action,
            "lot_size": point.lot_size,
            "margin_required": point.margin_required,
        }

        if action == "BUY" and entry is None:
            if account.reserve(key, point.margin_required):
                entries[key] = point
                signal_record["execution_status"] = "executed"
            else:
                signal_record.update({
                    "execution_status": "blocked",
                    "blocked_reason": "insufficient_available_capital",
                    "required_margin": max(float(point.margin_required), 0.0),
                    "available_capital": account.available_capital,
                })

        entry = entries.get(key)
        exit_reason: str | None = "strategy" if action == "SELL" and entry is not None else None
        if entry is not None and point.expiry_date is not None and point.timestamp.date() >= point.expiry_date:
            exit_reason = "expiry"

        signals.append(signal_record)

        if exit_reason is not None and entry is not None:
            gross = _unrealized_profit(entry, point, execution_model)
            net = gross - charges_per_trade - funding_cost_per_trade
            account.apply_realized_pnl(net)
            reserved_margin = account.release(key)
            trades.append({
                "entry_time": entry.timestamp.isoformat(),
                "exit_time": point.timestamp.isoformat(),
                "symbol": entry.symbol,
                "contract_month": entry.contract_month,
                "lot_size": entry.lot_size,
                "gross_profit": gross,
                "charges": charges_per_trade,
                "funding_cost": funding_cost_per_trade,
                "net_profit": net,
                "execution_model": execution_model,
                "exit_reason": exit_reason,
                "reserved_margin": reserved_margin,
            })
            entries.pop(key, None)

        unrealized = sum(
            _unrealized_profit(open_entry, point, execution_model)
            for open_key, open_entry in entries.items()
            if open_key == key
        )
        equity.append({
            "timestamp": point.timestamp.isoformat(),
            "equity": float(account.realized_capital) + unrealized,
            "realized_capital": float(account.realized_capital),
            "unrealized_pnl": unrealized,
            "available_capital": account.available_capital,
            "reserved_margin": account.reserved_margin,
            "open_position_count": account.open_position_count,
        })

    final_unrealized = 0.0
    if ordered:
        last_by_key: dict[PositionKey, CashFutureHistoryPoint] = {}
        for point in ordered:
            last_by_key[(point.symbol, point.contract_month)] = point
        final_unrealized = sum(
            _unrealized_profit(entry, last_by_key[key], execution_model)
            for key, entry in entries.items()
        )

    return CashFuturePortfolioStrategyRun(
        initial_capital,
        float(account.realized_capital) + final_unrealized,
        float(account.realized_capital) + final_unrealized - initial_capital,
        tuple(signals),
        tuple(trades),
        tuple(equity),
        account.available_capital,
        account.reserved_margin,
        account.blocked_entries,
        account.open_position_count,
    )


__all__ = ["CashFuturePortfolioStrategyRun", "PortfolioStrategy", "run_cash_future_portfolio_strategy"]
