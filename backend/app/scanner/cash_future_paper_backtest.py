"""Deterministic Cash-Future paper backtest over synchronized 1-minute bars."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable

from app.scanner.synchronized_replay import ReplayBar


@dataclass(frozen=True)
class PaperBacktestConfig:
    starting_capital: float = 10_000_000.0
    min_entry_gap: float = 0.0
    exit_gap: float = 0.0
    charges_per_leg: float = 0.0
    funding_cost_per_day: float = 0.0
    future_selection: str = "BOTH"
    max_holding_days: int = 30
    collect_ledger: bool = True

    def __post_init__(self) -> None:
        for name in ("starting_capital", "min_entry_gap", "exit_gap", "charges_per_leg", "funding_cost_per_day"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.starting_capital <= 0:
            raise ValueError("starting_capital must be finite and positive")
        if self.max_holding_days <= 0:
            raise ValueError("max_holding_days must be positive")
        if self.future_selection.upper() not in {"CURRENT", "NEAR", "BOTH"}:
            raise ValueError("future_selection must be CURRENT, NEAR or BOTH")


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
    """Paper backtest with selectable future legs and bounded memory mode."""
    selection = config.future_selection.upper()
    capital = config.starting_capital
    peak = capital
    max_drawdown = 0.0
    entry: ReplayBar | None = None
    entry_time = None
    spot_entry = current_entry = near_entry = None
    entry_lot_size = None
    current_closed = selection == "NEAR"
    near_closed = selection == "CURRENT"
    realized = 0.0
    ledger: list[dict] = []
    current_exit_time = near_exit_time = None
    last_gross = 0.0

    for bar in bars:
        if not isinstance(bar.lot_size, int) or bar.lot_size <= 0:
            raise ValueError("lot_size must be a positive integer")
        numeric_values = (bar.spot, bar.current_future, bar.near_future, bar.current_gap, bar.near_gap)
        if not all(math.isfinite(float(value)) for value in numeric_values):
            raise ValueError("replay bar contains non-finite market values")
        if cancelled is not None and cancelled():
            return _result_with_ledger({"status": "cancelled", "starting_capital": capital, "ending_capital": capital + realized, "net_profit": realized}, ledger, config.collect_ledger)

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
            entry_lot_size = bar.lot_size
            continue

        assert entry_time is not None and spot_entry is not None and current_entry is not None and near_entry is not None and entry_lot_size is not None
        if (bar.timestamp - entry_time).days >= config.max_holding_days:
            if selection in {"CURRENT", "BOTH"} and not current_closed:
                realized += _short_pnl(current_entry, bar.current_future, entry_lot_size)
                current_closed = True
                current_exit_time = bar.timestamp
            if selection in {"NEAR", "BOTH"} and not near_closed:
                realized += _short_pnl(near_entry, bar.near_future, entry_lot_size)
                near_closed = True
                near_exit_time = bar.timestamp

        if selection in {"CURRENT", "BOTH"} and not current_closed and (bar.current_gap <= config.exit_gap or bar.timestamp.date() >= bar.current_expiry):
            realized += _short_pnl(current_entry, bar.current_future, entry_lot_size)
            current_closed = True
            current_exit_time = bar.timestamp

        if selection in {"NEAR", "BOTH"} and not near_closed and bar.timestamp.date() >= bar.near_expiry:
            realized += _short_pnl(near_entry, bar.near_future, entry_lot_size)
            near_closed = True
            near_exit_time = bar.timestamp

        spot_pnl = (bar.spot - spot_entry) * entry_lot_size
        current_pnl = 0.0 if current_closed else _short_pnl(current_entry, bar.current_future, entry_lot_size)
        near_pnl = 0.0 if near_closed else _short_pnl(near_entry, bar.near_future, entry_lot_size)
        gross = realized + spot_pnl + current_pnl + near_pnl
        last_gross = gross
        equity = capital + gross
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

        if config.collect_ledger:
            ledger.append({"timestamp": bar.timestamp.isoformat(), "spot": bar.spot, "current_future": bar.current_future, "near_future": bar.near_future, "current_gap": bar.current_gap, "near_gap": bar.near_gap, "future_selection": selection, "lot_size": entry_lot_size, "spot_pnl": spot_pnl, "current_future_pnl": current_pnl, "near_future_pnl": near_pnl, "gross_profit": gross, "equity": equity})

        if current_closed and near_closed:
            days_held = max(0.0, (bar.timestamp - entry_time).total_seconds() / 86400.0)
            funding = days_held * config.funding_cost_per_day
            future_legs = 1 if selection in {"CURRENT", "NEAR"} else 2
            charges = config.charges_per_leg * (1 + future_legs)
            net = gross - funding - charges
            return _result_with_ledger({"status": "completed", "future_selection": selection, "starting_capital": capital, "ending_capital": capital + net, "net_profit": net, "roi_pct": net / capital * 100.0, "max_drawdown": max_drawdown, "entry_time": entry_time.isoformat(), "exit_time": bar.timestamp.isoformat(), "entry_current_gap": entry.current_gap, "entry_near_gap": entry.near_gap, "lot_size": entry_lot_size, "charges": charges, "funding_cost": funding}, ledger, config.collect_ledger)

    if entry is None:
        return _result_with_ledger({"status": "no_entry", "future_selection": selection, "starting_capital": capital, "ending_capital": capital, "net_profit": 0.0}, ledger, config.collect_ledger)

    return _result_with_ledger({"status": "open", "future_selection": selection, "starting_capital": capital, "ending_capital": capital + last_gross, "net_profit": last_gross, "roi_pct": last_gross / capital * 100.0, "max_drawdown": max_drawdown, "entry_time": entry_time.isoformat(), "entry_current_gap": entry.current_gap, "entry_near_gap": entry.near_gap, "lot_size": entry_lot_size, "current_exit_time": current_exit_time.isoformat() if current_exit_time else None, "near_exit_time": near_exit_time.isoformat() if near_exit_time else None}, ledger, config.collect_ledger)
