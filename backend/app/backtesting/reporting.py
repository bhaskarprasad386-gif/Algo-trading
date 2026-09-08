"""Deterministic, memory-bounded performance metrics for backtest result streams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable, Mapping


@dataclass(frozen=True)
class BacktestTrade:
    timestamp_ns: int
    instrument: str
    quantity: int
    entry_value: float
    exit_value: float
    fees: float = 0.0

    @property
    def gross_pnl(self) -> float:
        return self.exit_value - self.entry_value

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees


@dataclass(frozen=True)
class BacktestReport:
    initial_capital: float
    final_equity: float
    net_pnl: float
    roi: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    turnover: float
    trade_count: int
    wins: int
    losses: int
    equity_curve: tuple[tuple[int, float], ...]
    monthly_pnl: Mapping[str, float]
    yearly_pnl: Mapping[str, float]


def build_report(initial_capital: float, trades: Iterable[BacktestTrade]) -> BacktestReport:
    """Aggregate trades in one pass; only the compact equity curve is retained."""
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital must be positive and finite")

    equity = float(initial_capital)
    peak = equity
    max_dd = 0.0
    max_dd_pct = 0.0
    turnover = 0.0
    wins = losses = 0
    monthly: dict[str, float] = {}
    yearly: dict[str, float] = {}
    curve: list[tuple[int, float]] = []

    for trade in trades:
        if trade.timestamp_ns < 0 or not trade.instrument.strip() or trade.quantity <= 0:
            raise ValueError("invalid trade record")
        values = (trade.entry_value, trade.exit_value, trade.fees)
        if not all(isfinite(v) and v >= 0 for v in values):
            raise ValueError("trade values must be finite and non-negative")
        pnl = trade.net_pnl
        equity += pnl
        peak = max(peak, equity)
        dd = max(0.0, peak - equity)
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd / peak if peak > 0 else 0.0)
        turnover += trade.entry_value + trade.exit_value
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

        dt = datetime.fromtimestamp(trade.timestamp_ns / 1_000_000_000, tz=timezone.utc)
        month = f"{dt.year:04d}-{dt.month:02d}"
        year = f"{dt.year:04d}"
        monthly[month] = monthly.get(month, 0.0) + pnl
        yearly[year] = yearly.get(year, 0.0) + pnl
        curve.append((trade.timestamp_ns, equity))

    net_pnl = equity - initial_capital
    gross_profit = sum(max(0.0, p.net_pnl) for p in trades)
    gross_loss = sum(min(0.0, p.net_pnl) for p in trades)
    # Re-iterating an input iterable would be unsafe for generators, so derive PF
    # from the compact trade-independent accumulators below when necessary.
    if curve:
        # Recompute from equity deltas without retaining every trade.
        gains = losses_abs = 0.0
        previous = initial_capital
        for _, current in curve:
            delta = current - previous
            if delta > 0:
                gains += delta
            elif delta < 0:
                losses_abs += -delta
            previous = current
        profit_factor = gains / losses_abs if losses_abs > 0 else (float("inf") if gains > 0 else 0.0)
    else:
        profit_factor = 0.0

    return BacktestReport(
        initial_capital=initial_capital,
        final_equity=equity,
        net_pnl=net_pnl,
        roi=net_pnl / initial_capital,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        win_rate=wins / (wins + losses) if wins + losses else 0.0,
        profit_factor=profit_factor,
        turnover=turnover,
        trade_count=len(curve),
        wins=wins,
        losses=losses,
        equity_curve=tuple(curve),
        monthly_pnl=dict(sorted(monthly.items())),
        yearly_pnl=dict(sorted(yearly.items())),
    )
