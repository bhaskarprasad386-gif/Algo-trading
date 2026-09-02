"""Deterministic Cash-Future convergence backtest.

Current and near contracts are intentionally processed independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.scanner.cash_future_history import CashFutureHistoryPoint


@dataclass(frozen=True)
class BacktestConfig:
    min_entry_gap: float = 0.0
    exit_gap: float = 0.0
    charges_per_trade: float = 0.0
    funding_cost_per_trade: float = 0.0
    max_holding_days: int = 30


@dataclass(frozen=True)
class BacktestTrade:
    entry_time: datetime
    exit_time: datetime
    entry_gap: float
    exit_gap: float
    lot_size: int
    gross_profit: float
    charges: float
    funding_cost: float
    net_profit: float
    roi_pct: float
    exit_reason: str


def run_backtest(points: Iterable[CashFutureHistoryPoint], config: BacktestConfig) -> dict:
    ordered = sorted(points, key=lambda p: p.timestamp)
    if not ordered:
        return {"trades": [], "trade_count": 0, "wins": 0, "win_rate_pct": 0.0, "net_profit": 0.0, "roi_pct": 0.0, "max_drawdown": 0.0}

    trades: list[BacktestTrade] = []
    entry = None
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for point in ordered:
        if entry is None and point.gap >= config.min_entry_gap:
            entry = point
            continue
        if entry is None:
            continue

        holding_days = (point.timestamp - entry.timestamp).total_seconds() / 86400.0
        converged = point.gap <= config.exit_gap
        expired = entry.expiry_date is not None and point.timestamp.date() >= entry.expiry_date
        timed_out = holding_days >= config.max_holding_days
        if not (converged or expired or timed_out):
            continue

        gross = (entry.gap - point.gap) * entry.lot_size
        charges = config.charges_per_trade
        funding = config.funding_cost_per_trade
        net = gross - charges - funding
        capital = entry.cash_price * entry.lot_size + entry.margin_required
        roi = net / capital * 100.0 if capital else 0.0
        reason = "convergence" if converged else ("expiry" if expired else "max_holding")
        trade = BacktestTrade(entry.timestamp, point.timestamp, entry.gap, point.gap, entry.lot_size, gross, charges, funding, net, roi, reason)
        trades.append(trade)
        equity += net
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        entry = None

    wins = sum(1 for t in trades if t.net_profit > 0)
    capital = sum(t.lot_size * ordered[i].cash_price for i, t in enumerate(trades) if i < len(ordered))
    return {
        "trades": [t.__dict__ for t in trades],
        "trade_count": len(trades),
        "wins": wins,
        "win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "net_profit": equity,
        "roi_pct": sum(t.roi_pct for t in trades),
        "max_drawdown": max_drawdown,
    }
