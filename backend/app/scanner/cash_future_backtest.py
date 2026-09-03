"""Deterministic Cash-Future convergence backtest.

Current and near contracts are intentionally processed independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.scanner.cash_future_history import CashFutureHistoryPoint


@dataclass(frozen=True)
class BacktestConfig:
    min_entry_gap: float = 0.0
    exit_gap: float = 0.0
    charges_per_trade: float = 0.0
    funding_cost_per_trade: float = 0.0
    max_holding_days: int = 30
    contract_month: str | None = None


def run_backtest(points: Iterable[CashFutureHistoryPoint], config: BacktestConfig) -> dict:
    """Process an already time-ordered iterable without materializing it.

    Database callers should provide rows ordered by timestamp. When
    ``contract_month`` is supplied, only that contract is processed. When it
    is omitted, mixed contract input is rejected as soon as a different
    contract is encountered rather than silently combining expiry series.
    """
    trades = []
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    total_capital = 0.0
    equity_curve = []
    entry = None
    seen_contract: str | None = None

    for point in points:
        if config.contract_month is not None and point.contract_month != config.contract_month:
            continue

        if seen_contract is None:
            seen_contract = point.contract_month
        elif config.contract_month is None and point.contract_month != seen_contract:
            raise ValueError("backtest input contains multiple contract months; run each contract separately")

        if entry is None:
            if point.gap >= config.min_entry_gap:
                entry = point
            equity_curve.append({"timestamp": point.timestamp.isoformat(), "equity": equity})
            continue

        holding_days = (point.timestamp - entry.timestamp).total_seconds() / 86400.0
        converged = point.gap <= config.exit_gap
        expired = entry.expiry_date is not None and point.timestamp.date() >= entry.expiry_date
        timed_out = holding_days >= config.max_holding_days
        if not (converged or expired or timed_out):
            equity_curve.append({"timestamp": point.timestamp.isoformat(), "equity": equity})
            continue

        gross = (entry.gap - point.gap) * entry.lot_size
        net = gross - config.charges_per_trade - config.funding_cost_per_trade
        capital = entry.cash_price * entry.lot_size + entry.margin_required
        roi = net / capital * 100.0 if capital else 0.0
        reason = "convergence" if converged else ("expiry" if expired else "max_holding")
        trades.append({
            "entry_time": entry.timestamp.isoformat(),
            "exit_time": point.timestamp.isoformat(),
            "entry_gap": entry.gap,
            "exit_gap": point.gap,
            "lot_size": entry.lot_size,
            "gross_profit": gross,
            "charges": config.charges_per_trade,
            "funding_cost": config.funding_cost_per_trade,
            "net_profit": net,
            "roi_pct": roi,
            "exit_reason": reason,
        })
        equity += net
        total_capital += capital
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        equity_curve.append({"timestamp": point.timestamp.isoformat(), "equity": equity})
        entry = None

    wins = sum(1 for t in trades if t["net_profit"] > 0)
    return {
        "trade_count": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "net_profit": equity,
        "roi_pct": equity / total_capital * 100.0 if total_capital else 0.0,
        "max_drawdown": max_drawdown,
        "equity_curve": equity_curve,
        "trades": trades,
    }
