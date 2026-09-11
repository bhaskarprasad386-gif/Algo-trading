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


def _unrealized_profit(entry: CashFutureHistoryPoint, current: CashFutureHistoryPoint, execution_model: str, quantity: float) -> float:
    if execution_model == "gap":
        return _legacy_gap_profit(entry, current) * (quantity / entry.lot_size)
    return _executable_spread_profit(entry, current) * (quantity / entry.lot_size)


def _historical_fill_capacity(point: CashFutureHistoryPoint, *, side: str, requested_quantity: float, execution_model: str) -> tuple[float, str]:
    """Return executable quantity without inventing liquidity.

    Partial fills are enabled only when all required historical depth quantities
    are genuinely present for the executable legs. Otherwise bid/ask mode keeps
    its legacy strict full-lot behavior; gap mode has no depth-aware fill model.
    """
    requested = max(float(requested_quantity), 0.0)
    if requested <= 0:
        return 0.0, "none"
    if execution_model != "bid_ask":
        return requested, "gap_analytical"

    required = (
        (point.cash_ask_qty, point.future_bid_qty)
        if side == "entry"
        else (point.cash_bid_qty, point.future_ask_qty)
    )
    if any(value is None for value in required):
        return requested, "strict_bid_ask_no_depth"
    capacity = min(max(float(value), 0.0) for value in required)
    return min(requested, capacity), "historical_depth"


def run_cash_future_portfolio_strategy(
    points: Iterable[CashFutureHistoryPoint],
    strategy: PortfolioStrategy,
    *,
    initial_capital: float = 100_000_000.0,
    execution_model: str = "gap",
    charges_per_trade: float = 0.0,
    funding_cost_per_trade: float = 0.0,
) -> CashFuturePortfolioStrategyRun:
    """Run one strategy chronologically with shared capital and portfolio MTM.

    Historical partial fills use only genuine bid/ask depth quantities. Missing
    depth never becomes synthetic liquidity. A partial position retains only
    the filled quantity and its proportional margin reservation.
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
    latest: dict[PositionKey, CashFutureHistoryPoint] = {}
    entries: dict[PositionKey, tuple[CashFutureHistoryPoint, float]] = {}
    signals: list[Mapping[str, Any]] = []
    trades: list[Mapping[str, Any]] = []
    equity: list[Mapping[str, Any]] = []

    for point in ordered:
        key: PositionKey = (point.symbol, point.contract_month)
        visible_history = tuple(history + [point])
        raw_signal = strategy(point, visible_history)
        history.append(point)
        latest[key] = point
        action = "NONE" if raw_signal is None else str(raw_signal).upper()
        if action not in {"BUY", "SELL", "HOLD", "NONE"}:
            raise ValueError("Cash-Future portfolio strategy must return BUY, SELL, HOLD, or NONE")

        entry_state = entries.get(key)
        signal_record: dict[str, Any] = {
            "timestamp": point.timestamp.isoformat(), "symbol": point.symbol,
            "contract_month": point.contract_month, "action": action,
            "lot_size": point.lot_size, "margin_required": point.margin_required,
        }

        if action == "BUY" and entry_state is None:
            requested_quantity = float(point.lot_size)
            fill_quantity, liquidity_source = _historical_fill_capacity(
                point, side="entry", requested_quantity=requested_quantity, execution_model=execution_model
            )
            if fill_quantity <= 0:
                signal_record.update({"execution_status": "no_fill", "requested_quantity": requested_quantity, "filled_quantity": 0.0, "liquidity_source": liquidity_source})
            else:
                proportional_margin = max(float(point.margin_required), 0.0) * fill_quantity / point.lot_size
                if account.reserve(key, proportional_margin):
                    entries[key] = (point, fill_quantity)
                    signal_record.update({
                        "execution_status": "executed" if fill_quantity >= requested_quantity else "partial_fill",
                        "requested_quantity": requested_quantity, "filled_quantity": fill_quantity,
                        "unfilled_quantity": requested_quantity - fill_quantity,
                        "liquidity_source": liquidity_source,
                        "reserved_margin": proportional_margin,
                    })
                else:
                    signal_record.update({
                        "execution_status": "blocked", "blocked_reason": "insufficient_available_capital",
                        "required_margin": proportional_margin, "available_capital": account.available_capital,
                        "requested_quantity": requested_quantity, "filled_quantity": 0.0,
                        "liquidity_source": liquidity_source,
                    })

        entry_state = entries.get(key)
        exit_reason: str | None = "strategy" if action == "SELL" and entry_state is not None else None
        if entry_state is not None and point.expiry_date is not None and point.timestamp.date() >= point.expiry_date:
            exit_reason = "expiry"
        signals.append(signal_record)

        if exit_reason is not None and entry_state is not None:
            entry, open_quantity = entry_state
            requested_exit = open_quantity
            fill_quantity, liquidity_source = _historical_fill_capacity(
                point, side="exit", requested_quantity=requested_exit, execution_model=execution_model
            )
            if fill_quantity > 0:
                gross = _unrealized_profit(entry, point, execution_model, fill_quantity)
                net = gross - charges_per_trade - funding_cost_per_trade
                account.apply_realized_pnl(net)
                released_margin = account.reservation(key) * fill_quantity / open_quantity
                account.release(key)
                remaining_quantity = open_quantity - fill_quantity
                if remaining_quantity > 0:
                    remaining_margin = max(float(entry.margin_required), 0.0) * remaining_quantity / entry.lot_size
                    account.reserve(key, remaining_margin)
                    entries[key] = (entry, remaining_quantity)
                else:
                    entries.pop(key, None)
                trades.append({
                    "entry_time": entry.timestamp.isoformat(), "exit_time": point.timestamp.isoformat(),
                    "symbol": entry.symbol, "contract_month": entry.contract_month,
                    "lot_size": entry.lot_size, "requested_quantity": requested_exit,
                    "filled_quantity": fill_quantity, "unfilled_quantity": remaining_quantity,
                    "gross_profit": gross, "charges": charges_per_trade,
                    "funding_cost": funding_cost_per_trade, "net_profit": net,
                    "execution_model": execution_model, "exit_reason": exit_reason,
                    "reserved_margin": released_margin, "liquidity_source": liquidity_source,
                    "fill_status": "filled" if remaining_quantity == 0 else "partial_fill",
                })
            else:
                signal_record.update({"exit_execution_status": "no_fill", "exit_requested_quantity": requested_exit, "exit_filled_quantity": 0.0, "liquidity_source": liquidity_source})

        unrealized_by_key = {
            open_key: _unrealized_profit(open_entry, latest[open_key], execution_model, open_quantity)
            for open_key, (open_entry, open_quantity) in entries.items()
            if open_key in latest
        }
        unrealized = sum(unrealized_by_key.values())
        marked_equity = float(account.realized_capital) + unrealized

        if entries and marked_equity < account.reserved_margin and key in entries:
            signal_record["margin_breach"] = True
            signal_record["margin_breach_equity"] = marked_equity
            signal_record["margin_breach_reserved_margin"] = account.reserved_margin
            liquidation_entry, liquidation_quantity = entries[key]
            gross = _unrealized_profit(liquidation_entry, point, execution_model, liquidation_quantity)
            net = gross - charges_per_trade - funding_cost_per_trade
            account.apply_realized_pnl(net)
            released_margin = account.release(key)
            trades.append({
                "entry_time": liquidation_entry.timestamp.isoformat(), "exit_time": point.timestamp.isoformat(),
                "symbol": liquidation_entry.symbol, "contract_month": liquidation_entry.contract_month,
                "lot_size": liquidation_entry.lot_size, "requested_quantity": liquidation_quantity,
                "filled_quantity": liquidation_quantity, "unfilled_quantity": 0.0,
                "gross_profit": gross, "charges": charges_per_trade,
                "funding_cost": funding_cost_per_trade, "net_profit": net,
                "execution_model": execution_model, "exit_reason": "margin_breach",
                "reserved_margin": released_margin, "liquidity_source": "current_observation",
                "fill_status": "filled",
            })
            entries.pop(key, None)
            unrealized = sum(
                _unrealized_profit(open_entry, latest[open_key], execution_model, open_quantity)
                for open_key, (open_entry, open_quantity) in entries.items()
                if open_key in latest
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

    final_unrealized = sum(
        _unrealized_profit(entry, latest[key], execution_model, quantity)
        for key, (entry, quantity) in entries.items()
        if key in latest
    )
    final_equity = float(account.realized_capital) + final_unrealized
    return CashFuturePortfolioStrategyRun(
        initial_capital, final_equity, final_equity - initial_capital,
        tuple(signals), tuple(trades), tuple(equity), account.available_capital,
        account.reserved_margin, account.blocked_entries, account.open_position_count,
    )


__all__ = ["CashFuturePortfolioStrategyRun", "PortfolioStrategy", "run_cash_future_portfolio_strategy"]
