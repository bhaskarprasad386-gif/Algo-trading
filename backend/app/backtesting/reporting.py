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


def build_cash_future_report(
    initial_capital: float,
    trades: Iterable[Mapping[str, object]],
    equity_curve: Iterable[Mapping[str, object]] = (),
) -> BacktestReport:
    """Build the standard report from persisted Cash-Future trade/equity records.

    The input is streamed; only the compact equity curve and period aggregates
    are retained. Cash-Future records already contain authoritative gross/net
    P&L, so no synthetic fill prices are reconstructed.
    """
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital must be positive and finite")

    trade_count = wins = losses = 0
    gains = losses_abs = turnover = 0.0
    monthly: dict[str, float] = {}
    yearly: dict[str, float] = {}
    curve: list[tuple[int, float]] = []

    for trade in trades:
        try:
            timestamp = str(trade["exit_time"])
            gross = float(trade["gross_profit"])
            net = float(trade["net_profit"])
            charges = float(trade.get("charges", 0.0))
            funding = float(trade.get("funding_cost", 0.0))
            lot = int(trade["lot_size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid Cash-Future trade record") from exc
        if lot <= 0 or not all(isfinite(v) for v in (gross, net, charges, funding)):
            raise ValueError("invalid Cash-Future trade values")
        if net != gross - charges - funding:
            raise ValueError("Cash-Future net_profit does not reconcile with gross_profit, charges and funding_cost")

        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        timestamp_ns = int(parsed.timestamp() * 1_000_000_000)
        if timestamp_ns < 0:
            raise ValueError("Cash-Future exit_time cannot be before epoch")

        for key in (
            "entry_cash_price", "entry_future_price",
            "exit_cash_price", "exit_future_price",
        ):
            value = float(trade.get(key, 0.0))
            if not isfinite(value) or value < 0:
                raise ValueError("invalid Cash-Future price")
        turnover += (
            (float(trade.get("entry_cash_price", 0.0)) + float(trade.get("entry_future_price", 0.0)))
            + (float(trade.get("exit_cash_price", 0.0)) + float(trade.get("exit_future_price", 0.0)))
        ) * lot

        trade_count += 1
        if net > 0:
            wins += 1
            gains += net
        elif net < 0:
            losses += 1
            losses_abs += -net
        monthly_key = f"{parsed.year:04d}-{parsed.month:02d}"
        yearly_key = f"{parsed.year:04d}"
        monthly[monthly_key] = monthly.get(monthly_key, 0.0) + net
        yearly[yearly_key] = yearly.get(yearly_key, 0.0) + net

    # Prefer persisted equity records for drawdown; this preserves margin and
    # execution semantics of the strategy runner.
    equity_points = []
    for item in equity_curve:
        timestamp = str(item["timestamp"])
        equity = float(item["equity"])
        if not isfinite(equity):
            raise ValueError("invalid Cash-Future equity value")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        equity_points.append((int(parsed.timestamp() * 1_000_000_000), equity))
    equity_points.sort()

    if equity_points:
        peak = float(initial_capital)
        max_dd = max_dd_pct = 0.0
        for timestamp_ns, equity in equity_points:
            peak = max(peak, equity)
            dd = max(0.0, peak - equity)
            max_dd = max(max_dd, dd)
            max_dd_pct = max(max_dd_pct, dd / peak if peak > 0 else 0.0)
        final_equity = equity_points[-1][1]
        curve = equity_points
    else:
        final_equity = initial_capital + sum(monthly.values())
        peak = max(initial_capital, final_equity)
        max_dd = max_dd_pct = 0.0

    net_pnl = final_equity - initial_capital
    return BacktestReport(
        initial_capital=initial_capital,
        final_equity=final_equity,
        net_pnl=net_pnl,
        roi=net_pnl / initial_capital,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        win_rate=wins / (wins + losses) if wins + losses else 0.0,
        profit_factor=gains / losses_abs if losses_abs > 0 else (float("inf") if gains > 0 else 0.0),
        turnover=turnover,
        trade_count=trade_count,
        wins=wins,
        losses=losses,
        equity_curve=tuple(curve),
        monthly_pnl=dict(sorted(monthly.items())),
        yearly_pnl=dict(sorted(yearly.items())),
    )


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
    gains = losses_abs = 0.0
    trade_count = 0
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
            gains += pnl
        elif pnl < 0:
            losses += 1
            losses_abs += -pnl

        dt = datetime.fromtimestamp(trade.timestamp_ns / 1_000_000_000, tz=timezone.utc)
        month = f"{dt.year:04d}-{dt.month:02d}"
        year = f"{dt.year:04d}"
        monthly[month] = monthly.get(month, 0.0) + pnl
        yearly[year] = yearly.get(year, 0.0) + pnl
        curve.append((trade.timestamp_ns, equity))
        trade_count += 1

    profit_factor = gains / losses_abs if losses_abs > 0 else (float("inf") if gains > 0 else 0.0)
    net_pnl = equity - initial_capital

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
        trade_count=trade_count,
        wins=wins,
        losses=losses,
        equity_curve=tuple(curve),
        monthly_pnl=dict(sorted(monthly.items())),
        yearly_pnl=dict(sorted(yearly.items())),
    )
