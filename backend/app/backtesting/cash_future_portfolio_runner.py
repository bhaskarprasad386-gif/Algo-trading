"""Portfolio-level Cash-Future strategy execution with simultaneous positions."""

from __future__ import annotations

import math
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


def _unrealized_profit(entry: CashFutureHistoryPoint, current: CashFutureHistoryPoint, execution_model: str, quantity: float) -> float:
    if execution_model == "gap":
        return _legacy_gap_profit(entry, current) * (quantity / entry.lot_size)
    exit_prices = (current.cash_bid, current.future_ask)
    if any(price is None or not math.isfinite(float(price)) or float(price) <= 0 for price in exit_prices):
        return 0.0
    return _executable_spread_profit(entry, current) * (quantity / entry.lot_size)


def _historical_fill_capacity(point: CashFutureHistoryPoint, *, side: str, requested_quantity: float, execution_model: str) -> tuple[float, str]:
    """Return executable quantity without inventing liquidity or prices."""
    requested = float(requested_quantity)
    if not math.isfinite(requested) or requested <= 0:
        return 0.0, "invalid_quantity"
    if execution_model != "bid_ask":
        return requested, "gap_analytical"

    if side == "entry":
        prices = (point.cash_ask, point.future_bid)
        required = (point.cash_ask_qty, point.future_bid_qty)
    else:
        prices = (point.cash_bid, point.future_ask)
        required = (point.cash_bid_qty, point.future_ask_qty)

    if any(value is None or not math.isfinite(float(value)) or float(value) <= 0 for value in prices):
        return 0.0, "missing_executable_quote"
    if any(value is None for value in required):
        return 0.0, "strict_bid_ask_no_depth"
    numeric_depth = tuple(float(value) for value in required)
    if any(not math.isfinite(value) or value < 0 for value in numeric_depth):
        return 0.0, "invalid_depth"
    capacity = min(numeric_depth)
    return min(requested, capacity), "historical_depth"


def _close_position(account: CashFuturePortfolioLedger, entries: dict[PositionKey, tuple[CashFutureHistoryPoint, float]], key: PositionKey, point: CashFutureHistoryPoint, *, execution_model: str, charges_per_trade: float, funding_cost_per_trade: float, exit_reason: str) -> dict[str, Any] | None:
    """Close as much as the observation can actually execute, atomically."""
    entry, quantity = entries[key]
    fill_quantity, liquidity_source = _historical_fill_capacity(point, side="exit", requested_quantity=quantity, execution_model=execution_model)
    if fill_quantity <= 0:
        return None
    reservation = account.reservation(key)
    if reservation is None:
        raise ValueError("open position has no capital reservation")
    released_margin = float(reservation.margin_required) * fill_quantity / quantity
    remaining = quantity - fill_quantity
    remaining_margin = float(reservation.margin_required) - released_margin
    if remaining < 0 or remaining_margin < 0:
        raise ValueError("partial close produced invalid remaining quantity or margin")

    gross = _unrealized_profit(entry, point, execution_model, fill_quantity)
    fraction = fill_quantity / quantity
    charges = charges_per_trade * fraction
    funding_cost = funding_cost_per_trade * fraction
    net = gross - charges - funding_cost

    # Validate the complete state transition before mutating either capital or entries.
    account.release(key)
    if remaining > 0 and not account.reserve(key, remaining_margin):
        raise ValueError("cannot restore capital reservation for unfilled Cash-Future quantity")
    account.apply_realized_pnl(net)
    if remaining > 0:
        entries[key] = (entry, remaining)
    else:
        entries.pop(key, None)

    return {
        "entry_time": entry.timestamp.isoformat(), "exit_time": point.timestamp.isoformat(),
        "symbol": entry.symbol, "contract_month": entry.contract_month,
        "lot_size": entry.lot_size, "requested_quantity": quantity,
        "filled_quantity": fill_quantity, "unfilled_quantity": remaining,
        "gross_profit": gross, "charges": charges,
        "funding_cost": funding_cost, "net_profit": net,
        "execution_model": execution_model, "exit_reason": exit_reason,
        "reserved_margin": released_margin, "liquidity_source": liquidity_source,
        "fill_status": "filled" if remaining == 0 else "partial_fill",
    }


def _marked_equity(account: CashFuturePortfolioLedger, entries: Mapping[PositionKey, tuple[CashFutureHistoryPoint, float]], latest: Mapping[PositionKey, CashFutureHistoryPoint], execution_model: str) -> float:
    unrealized = sum(_unrealized_profit(open_entry, latest[open_key], execution_model, open_quantity) for open_key, (open_entry, open_quantity) in entries.items() if open_key in latest)
    return float(account.realized_capital) + unrealized


def run_cash_future_portfolio_strategy(points: Iterable[CashFutureHistoryPoint], strategy: PortfolioStrategy, *, initial_capital: float = 100_000_000.0, execution_model: str = "gap", charges_per_trade: float = 0.0, funding_cost_per_trade: float = 0.0, rollover_policy: str = "force_exit") -> CashFuturePortfolioStrategyRun:
    """Run chronologically with shared capital and explicit rollover policy."""
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if execution_model not in {"gap", "bid_ask"}:
        raise ValueError("execution_model must be 'gap' or 'bid_ask'")
    if rollover_policy not in {"force_exit", "reject"}:
        raise ValueError("rollover_policy must be 'force_exit' or 'reject'")
    if charges_per_trade < 0 or funding_cost_per_trade < 0:
        raise ValueError("charges_per_trade and funding_cost_per_trade must be non-negative")
    ordered = tuple(points)
    if any(current.timestamp < previous.timestamp for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("Cash-Future portfolio input must be ordered by timestamp")

    account = CashFuturePortfolioLedger(initial_capital)
    history: list[CashFutureHistoryPoint] = []
    latest: dict[PositionKey, CashFutureHistoryPoint] = {}
    active_contract: dict[str, str] = {}
    entries: dict[PositionKey, tuple[CashFutureHistoryPoint, float]] = {}
    signals: list[Mapping[str, Any]] = []
    trades: list[Mapping[str, Any]] = []
    equity: list[Mapping[str, Any]] = []

    for point in ordered:
        key: PositionKey = (point.symbol, point.contract_month)
        previous_contract = active_contract.get(point.symbol)
        if previous_contract is not None and previous_contract != point.contract_month:
            old_key = (point.symbol, previous_contract)
            if old_key in entries:
                if rollover_policy == "reject":
                    raise ValueError("open Cash-Future position crossed a contract rollover boundary")
                old_point = latest.get(old_key)
                if old_point is None:
                    raise ValueError("cannot roll over without a genuine last observation for the old contract")
                rollover_trade = _close_position(account, entries, old_key, old_point, execution_model=execution_model, charges_per_trade=charges_per_trade, funding_cost_per_trade=funding_cost_per_trade, exit_reason="rollover")
                if rollover_trade is None:
                    raise ValueError("rollover requires executable liquidity at the old contract boundary")
                trades.append(rollover_trade)
            active_contract[point.symbol] = point.contract_month
        elif previous_contract is None:
            active_contract[point.symbol] = point.contract_month

        visible_history = tuple(history + [point])
        raw_signal = strategy(point, visible_history)
        history.append(point)
        latest[key] = point
        action = "NONE" if raw_signal is None else str(raw_signal).upper()
        if action not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("Cash-Future portfolio strategy must return BUY, SELL, HOLD, or NONE")

        entry_state = entries.get(key)
        signal_record: dict[str, Any] = {"timestamp": point.timestamp.isoformat(), "symbol": point.symbol, "contract_month": point.contract_month, "action": action, "lot_size": point.lot_size, "margin_required": point.margin_required}

        if action == "BUY" and entry_state is None:
            requested_quantity = float(point.lot_size)
            fill_quantity, liquidity_source = _historical_fill_capacity(point, side="entry", requested_quantity=requested_quantity, execution_model=execution_model)
            if fill_quantity <= 0:
                signal_record.update({"execution_status": "no_fill", "requested_quantity": requested_quantity, "filled_quantity": 0.0, "liquidity_source": liquidity_source})
            else:
                proportional_margin = max(float(point.margin_required), 0.0) * fill_quantity / point.lot_size
                if account.reserve(key, proportional_margin):
                    entries[key] = (point, fill_quantity)
                    signal_record.update({"execution_status": "executed" if fill_quantity >= requested_quantity else "partial_fill", "requested_quantity": requested_quantity, "filled_quantity": fill_quantity, "unfilled_quantity": requested_quantity - fill_quantity, "liquidity_source": liquidity_source, "reserved_margin": proportional_margin})
                else:
                    signal_record.update({"execution_status": "blocked", "blocked_reason": "insufficient_available_capital", "required_margin": proportional_margin, "available_capital": account.available_capital, "requested_quantity": requested_quantity, "filled_quantity": 0.0, "liquidity_source": liquidity_source})

        entry_state = entries.get(key)
        exit_reason: str | None = "strategy" if action == "SELL" and entry_state is not None else None
        if entry_state is not None and point.expiry_date is not None and point.timestamp.date() >= point.expiry_date:
            exit_reason = "expiry"
        signals.append(signal_record)

        if exit_reason is not None and entry_state is not None:
            trade = _close_position(account, entries, key, point, execution_model=execution_model, charges_per_trade=charges_per_trade, funding_cost_per_trade=funding_cost_per_trade, exit_reason=exit_reason)
            if trade is not None:
                trades.append(trade)
            else:
                signal_record.update({"exit_execution_status": "no_fill", "exit_requested_quantity": entry_state[1], "exit_filled_quantity": 0.0})

        unrealized = sum(_unrealized_profit(open_entry, latest[open_key], execution_model, open_quantity) for open_key, (open_entry, open_quantity) in entries.items() if open_key in latest)
        marked_equity = float(account.realized_capital) + unrealized
        if entries and marked_equity < account.reserved_margin:
            signal_record["margin_breach"] = True
            signal_record["margin_breach_equity"] = marked_equity
            signal_record["margin_breach_reserved_margin"] = account.reserved_margin
            for liquidation_key in tuple(entries):
                liquidation_point = latest.get(liquidation_key)
                if liquidation_point is None:
                    continue
                liquidation_trade = _close_position(account, entries, liquidation_key, liquidation_point, execution_model=execution_model, charges_per_trade=charges_per_trade, funding_cost_per_trade=funding_cost_per_trade, exit_reason="margin_breach")
                if liquidation_trade is not None:
                    trades.append(liquidation_trade)

        if entries and _marked_equity(account, entries, latest, execution_model) < account.reserved_margin:
            raise ValueError("portfolio margin breach remains after liquidation; current observations cannot safely liquidate all open positions")

        unrealized = sum(_unrealized_profit(open_entry, latest[open_key], execution_model, open_quantity) for open_key, (open_entry, open_quantity) in entries.items() if open_key in latest)
        equity.append({"timestamp": point.timestamp.isoformat(), "equity": float(account.realized_capital) + unrealized, "realized_capital": float(account.realized_capital), "unrealized_pnl": unrealized, "available_capital": account.available_capital, "reserved_margin": account.reserved_margin, "open_position_count": account.open_position_count})

    final_unrealized = sum(_unrealized_profit(entry, latest[key], execution_model, quantity) for key, (entry, quantity) in entries.items() if key in latest)
    final_equity = float(account.realized_capital) + final_unrealized
    return CashFuturePortfolioStrategyRun(initial_capital, final_equity, final_equity - initial_capital, tuple(signals), tuple(trades), tuple(equity), account.available_capital, account.reserved_margin, account.blocked_entries, account.open_position_count)


__all__ = ["CashFuturePortfolioStrategyRun", "PortfolioStrategy", "run_cash_future_portfolio_strategy"]
