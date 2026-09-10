"""Deterministic Cash-Future convergence backtest.

Each futures contract is backtested independently; the multi-contract runner
combines their realized results without mixing expiry series.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from operator import attrgetter
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
    """Backtest one contract without materializing the input iterable."""
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
        elif point.contract_month != seen_contract:
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
        "invested_capital": total_capital,
        "max_drawdown": max_drawdown,
        "equity_curve": equity_curve,
        "trades": trades,
    }


def _aggregate_contract_results(results: list[dict]) -> dict:
    """Combine independent contract results without mixing observations."""
    trades = [trade for result in results for trade in result["trades"]]
    trades.sort(key=lambda trade: trade["entry_time"])
    wins = sum(1 for trade in trades if trade["net_profit"] > 0)
    net_profit = sum(trade["net_profit"] for trade in trades)
    invested_capital = sum(result["invested_capital"] for result in results)

    equity = 0.0
    running_peak = 0.0
    max_drawdown = 0.0
    equity_curve = []
    for trade in trades:
        equity += trade["net_profit"]
        running_peak = max(running_peak, equity)
        max_drawdown = max(max_drawdown, running_peak - equity)
        equity_curve.append({"timestamp": trade["exit_time"], "equity": equity})

    return {
        "contract_count": len(results),
        "trade_count": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": wins / len(trades) * 100.0 if trades else 0.0,
        "net_profit": net_profit,
        "roi_pct": net_profit / invested_capital * 100.0 if invested_capital else 0.0,
        "invested_capital": invested_capital,
        "max_drawdown": max_drawdown,
        "equity_curve": equity_curve,
        "trades": trades,
        "per_contract": results,
    }


def run_multi_contract_backtest(
    points: Iterable[CashFutureHistoryPoint],
    config: BacktestConfig,
) -> dict:
    """Backtest all contracts independently and aggregate realized results."""
    grouped: dict[str, list[CashFutureHistoryPoint]] = {}
    for point in points:
        if config.contract_month is not None and point.contract_month != config.contract_month:
            continue
        grouped.setdefault(point.contract_month, []).append(point)

    results = [run_backtest(grouped[contract], config) for contract in sorted(grouped)]
    return _aggregate_contract_results(results)


def run_multi_contract_backtest_streaming(
    points: Iterable[CashFutureHistoryPoint],
    config: BacktestConfig,
) -> dict:
    """Backtest ordered contract streams without materializing the history period.

    The input must be ordered by ``contract_month`` and then timestamp, as the
    persisted Cash-Future coverage store provides. Only one contract's points
    are consumed by ``run_backtest`` at a time.
    """
    filtered = (
        point
        for point in points
        if config.contract_month is None or point.contract_month == config.contract_month
    )
    results: list[dict] = []
    for _, contract_points in groupby(filtered, key=attrgetter("contract_month")):
        results.append(run_backtest(contract_points, config))
    return _aggregate_contract_results(results)


__all__ = [
    "BacktestConfig",
    "run_backtest",
    "run_multi_contract_backtest",
    "run_multi_contract_backtest_streaming",
]
