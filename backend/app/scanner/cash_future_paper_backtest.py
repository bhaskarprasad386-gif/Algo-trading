"""Deterministic Cash-Future paper backtest over synchronized 1-minute bars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.scanner.synchronized_replay import ReplayBar


@dataclass(frozen=True)
class PaperBacktestConfig:
    starting_capital: float = 2_000_000.0
    min_entry_gap: float = 0.0
    exit_gap: float = 0.0
    charges_per_leg: float = 0.0
    funding_cost_per_day: float = 0.0


def _short_pnl(entry: float, mark: float, lot_size: int) -> float:
    return (entry - mark) * lot_size


def run_cash_future_paper_backtest(
    bars: Iterable[ReplayBar],
    config: PaperBacktestConfig,
) -> dict:
    """Paper trade Spot BUY + Current Future SELL + Near Future SELL.

    Bars are chronological synchronized 1-minute observations. No future bar
    is consulted when deciding entry. Current and near legs settle using their
    historical expiry dates. The source remains minute-level; this engine does
    not aggregate the input.
    """
    capital = config.starting_capital
    peak = capital
    max_drawdown = 0.0
    entry: ReplayBar | None = None
    entry_time = None
    spot_entry = current_entry = near_entry = None
    current_closed = near_closed = False
    realized = 0.0
    ledger: list[dict] = []

    for bar in bars:
        if entry is None:
            if bar.current_gap < config.min_entry_gap:
                continue
            entry = bar
            entry_time = bar.timestamp
            spot_entry = bar.spot
            current_entry = bar.current_future
            near_entry = bar.near_future
            continue

        assert entry_time is not None
        assert spot_entry is not None and current_entry is not None and near_entry is not None

        if not current_closed and (bar.current_gap <= config.exit_gap or bar.timestamp.date() >= bar.current_expiry):
            realized += _short_pnl(current_entry, bar.current_future, bar.lot_size)
            current_closed = True
            current_exit_time = bar.timestamp
        if not near_closed and bar.timestamp.date() >= bar.near_expiry:
            realized += _short_pnl(near_entry, bar.near_future, bar.lot_size)
            near_closed = True
            near_exit_time = bar.timestamp

        spot_pnl = (bar.spot - spot_entry) * bar.lot_size
        current_pnl = 0.0 if current_closed else _short_pnl(current_entry, bar.current_future, bar.lot_size)
        near_pnl = 0.0 if near_closed else _short_pnl(near_entry, bar.near_future, bar.lot_size)
        gross = realized + spot_pnl + current_pnl + near_pnl
        equity = capital + gross
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

        ledger.append({
            "timestamp": bar.timestamp.isoformat(),
            "spot": bar.spot,
            "current_future": bar.current_future,
            "near_future": bar.near_future,
            "current_gap": bar.current_gap,
            "near_gap": bar.near_gap,
            "lot_size": bar.lot_size,
            "spot_pnl": spot_pnl,
            "current_future_pnl": current_pnl,
            "near_future_pnl": near_pnl,
            "gross_profit": gross,
            "equity": equity,
        })

        if current_closed and near_closed:
            days_held = max(0.0, (bar.timestamp - entry_time).total_seconds() / 86400.0)
            funding = days_held * config.funding_cost_per_day
            charges = config.charges_per_leg * 3.0
            net = gross - funding - charges
            return {
                "status": "completed",
                "starting_capital": capital,
                "ending_capital": capital + net,
                "net_profit": net,
                "roi_pct": net / capital * 100.0 if capital else 0.0,
                "max_drawdown": max_drawdown,
                "entry_time": entry_time.isoformat(),
                "exit_time": bar.timestamp.isoformat(),
                "entry_current_gap": entry.current_gap,
                "entry_near_gap": entry.near_gap,
                "lot_size": bar.lot_size,
                "charges": charges,
                "funding_cost": funding,
                "ledger": ledger,
            }

    if entry is None:
        return {"status": "no_entry", "starting_capital": capital, "ending_capital": capital, "net_profit": 0.0, "ledger": []}

    return {
        "status": "open",
        "starting_capital": capital,
        "ending_capital": capital + realized,
        "net_profit": realized,
        "roi_pct": realized / capital * 100.0 if capital else 0.0,
        "max_drawdown": max_drawdown,
        "entry_time": entry_time.isoformat() if entry_time else None,
        "entry_current_gap": entry.current_gap,
        "entry_near_gap": entry.near_gap,
        "lot_size": entry.lot_size,
        "ledger": ledger,
    }
