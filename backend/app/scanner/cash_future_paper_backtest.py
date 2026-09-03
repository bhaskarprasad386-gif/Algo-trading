"""Deterministic Cash-Future paper backtest over synchronized 1-minute bars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from app.scanner.synchronized_replay import ReplayBar


@dataclass(frozen=True)
class PaperBacktestConfig:
    starting_capital: float = 2_000_000.0
    min_entry_gap: float = 0.0
    exit_gap: float = 0.0
    charges_per_leg: float = 0.0
    funding_cost_per_day: float = 0.0
    future_selection: str = "BOTH"
    max_holding_days: int = 30
    collect_ledger: bool = True


def _short_pnl(entry: float, mark: float, lot_size: int) -> float:
    return (entry - mark) * lot_size


def _result_with_ledger(result: dict, ledger: list[dict], collect_ledger: bool) -> dict:
    if collect_ledger:
        result["ledger"] = ledger
    return result


def run_cash_future_paper_backtest(
    bars: Iterable[ReplayBar],
    config: PaperBacktestConfig,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Paper backtest with selectable future legs and bounded memory mode.

    ``collect_ledger=False`` is used by the full-F&O background worker. In that
    mode every minute is consumed from the replay iterator but is not retained in
    RAM. The canonical minute data remains in the persistent historical store.
    """
    selection = config.future_selection.upper()
    if selection not in {"CURRENT", "NEAR", "BOTH"}:
        raise ValueError("future_selection must be CURRENT, NEAR or BOTH")

    capital = config.starting_capital
    peak = capital
    max_drawdown = 0.0
    entry: ReplayBar | None = None
    entry_time = None
    spot_entry = current_entry = near_entry = None
    current_closed = selection == "NEAR"
    near_closed = selection == "CURRENT"
    realized = 0.0
    ledger: list[dict] = []
    current_exit_time = near_exit_time = None

    for bar in bars:
        if cancelled is not None and cancelled():
            return _result_with_ledger({
                "status": "cancelled",
                "starting_capital": capital,
                "ending_capital": capital + realized,
                "net_profit": realized,
            }, ledger, config.collect_ledger)

        if entry is None:
            trigger_gap = bar.current_gap if selection == "CURRENT" else bar.near_gap
            if selection == "BOTH":
                trigger_gap = min(bar.current_gap, bar.near_gap)
            if trigger_gap < config.min_entry_gap:
                continue
            entry = bar
            entry_time = bar.timestamp
            spot_entry = bar.spot
            current_entry = bar.current_future
            near_entry = bar.near_future
            continue

        assert entry_time is not None and spot_entry is not None
        assert current_entry is not None and near_entry is not None

        if config.max_holding_days > 0 and (bar.timestamp - entry_time).days >= config.max_holding_days:
            if selection in {"CURRENT", "BOTH"} and not current_closed:
                realized += _short_pnl(current_entry, bar.current_future, bar.lot_size)
                current_closed = True
                current_exit_time = bar.timestamp
            if selection in {"NEAR", "BOTH"} and not near_closed:
                realized += _short_pnl(near_entry, bar.near_future, bar.lot_size)
                near_closed = True
                near_exit_time = bar.timestamp

        if selection in {"CURRENT", "BOTH"} and not current_closed:
            if bar.current_gap <= config.exit_gap or bar.timestamp.date() >= bar.current_expiry:
                realized += _short_pnl(current_entry, bar.current_future, bar.lot_size)
                current_closed = True
                current_exit_time = bar.timestamp

        if selection in {"NEAR", "BOTH"} and not near_closed:
            if bar.timestamp.date() >= bar.near_expiry:
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

        if config.collect_ledger:
            ledger.append({
                "timestamp": bar.timestamp.isoformat(),
                "spot": bar.spot,
                "current_future": bar.current_future,
                "near_future": bar.near_future,
                "current_gap": bar.current_gap,
                "near_gap": bar.near_gap,
                "future_selection": selection,
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
            future_legs = 1 if selection in {"CURRENT", "NEAR"} else 2
            charges = config.charges_per_leg * (1 + future_legs)
            net = gross - funding - charges
            return _result_with_ledger({
                "status": "completed",
                "future_selection": selection,
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
            }, ledger, config.collect_ledger)

    if entry is None:
        return _result_with_ledger({
            "status": "no_entry",
            "future_selection": selection,
            "starting_capital": capital,
            "ending_capital": capital,
            "net_profit": 0.0,
        }, ledger, config.collect_ledger)

    return _result_with_ledger({
        "status": "open",
        "future_selection": selection,
        "starting_capital": capital,
        "ending_capital": capital + realized,
        "net_profit": realized,
        "roi_pct": realized / capital * 100.0 if capital else 0.0,
        "max_drawdown": max_drawdown,
        "entry_time": entry_time.isoformat() if entry_time else None,
        "entry_current_gap": entry.current_gap,
        "entry_near_gap": entry.near_gap,
        "lot_size": entry.lot_size,
        "current_exit_time": current_exit_time.isoformat() if current_exit_time else None,
        "near_exit_time": near_exit_time.isoformat() if near_exit_time else None,
    }, ledger, config.collect_ledger)
