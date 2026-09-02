"""Historical Cash-Future matching and graph-series helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable


@dataclass(frozen=True)
class CashFutureHistoryPoint:
    timestamp: datetime
    symbol: str
    contract_month: str
    cash_price: float
    future_price: float
    gap: float
    gap_pct: float
    lot_size: int
    margin_required: float
    volume: float | None = None
    oi: float | None = None
    cash_bid: float | None = None
    cash_ask: float | None = None
    future_bid: float | None = None
    future_ask: float | None = None
    charges: float = 0.0
    funding_cost: float = 0.0
    net_profit: float = 0.0
    roi_pct: float = 0.0
    expiry_date: date | None = None


@dataclass(frozen=True)
class HistoricalGapMatch:
    timestamp: datetime
    symbol: str
    contract_month: str
    gap: float
    gap_pct: float
    net_profit: float
    roi_pct: float
    difference_from_target: float


@dataclass(frozen=True)
class HistoricalGapOutcome:
    match: HistoricalGapMatch
    exit_timestamp: datetime | None
    exit_gap: float | None
    duration_days: float | None
    exit_reason: str | None
    convergence_profit: float | None
    convergence_roi_pct: float | None


def find_historical_gap_matches(points: Iterable[CashFutureHistoryPoint], target_gap: float, tolerance: float = 0.0, contract_month: str | None = None) -> list[HistoricalGapMatch]:
    lower_bound = target_gap - max(tolerance, 0.0)
    matches = []
    for point in points:
        if contract_month is not None and point.contract_month != contract_month:
            continue
        if point.gap < lower_bound:
            continue
        matches.append(HistoricalGapMatch(point.timestamp, point.symbol, point.contract_month, point.gap, point.gap_pct, point.net_profit, point.roi_pct, point.gap - target_gap))
    return sorted(matches, key=lambda item: item.timestamp, reverse=True)


def analyze_historical_gap_outcomes(
    points: Iterable[CashFutureHistoryPoint],
    target_gap: float,
    tolerance: float = 0.0,
    contract_month: str | None = None,
    exit_gap: float = 0.0,
    max_holding_days: int = 30,
    charges_per_trade: float = 0.0,
    funding_cost_per_trade: float = 0.0,
) -> list[HistoricalGapOutcome]:
    """Find prior gap occurrences and measure the first subsequent exit.

    Each occurrence is evaluated only against later observations from the same
    symbol and contract month. The exit is the first observation at/below
    ``exit_gap``, expiry, or ``max_holding_days``. No CURRENT/NEAR mixing occurs.
    """
    ordered = sorted(points, key=lambda p: p.timestamp)
    matches = find_historical_gap_matches(ordered, target_gap, tolerance, contract_month)
    outcomes: list[HistoricalGapOutcome] = []

    for match in matches:
        later = [
            p for p in ordered
            if p.symbol == match.symbol
            and p.contract_month == match.contract_month
            and p.timestamp > match.timestamp
        ]
        exit_point = None
        exit_reason = None
        for point in later:
            holding_days = (point.timestamp - match.timestamp).total_seconds() / 86400.0
            expired = point.expiry_date is not None and point.timestamp.date() >= point.expiry_date
            if point.gap <= exit_gap:
                exit_point = point
                exit_reason = "convergence"
                break
            if expired:
                exit_point = point
                exit_reason = "expiry"
                break
            if holding_days >= max_holding_days:
                exit_point = point
                exit_reason = "max_holding"
                break

        if exit_point is None:
            outcomes.append(HistoricalGapOutcome(match, None, None, None, None, None, None))
            continue

        duration_days = (exit_point.timestamp - match.timestamp).total_seconds() / 86400.0
        gross = (match.gap - exit_point.gap) * max(1, next(
            p.lot_size for p in later if p.timestamp == exit_point.timestamp
        ))
        net = gross - charges_per_trade - funding_cost_per_trade
        entry_point = next(p for p in ordered if p.timestamp == match.timestamp and p.symbol == match.symbol and p.contract_month == match.contract_month)
        capital = entry_point.cash_price * entry_point.lot_size + entry_point.margin_required
        roi = net / capital * 100.0 if capital else 0.0
        outcomes.append(HistoricalGapOutcome(match, exit_point.timestamp, exit_point.gap, duration_days, exit_reason, net, roi))

    return outcomes


def build_graph_series(points: Iterable[CashFutureHistoryPoint], contract_month: str | None = None) -> dict[str, list]:
    selected = [p for p in points if contract_month is None or p.contract_month == contract_month]
    selected.sort(key=lambda p: p.timestamp)
    return {
        "timestamps": [p.timestamp.isoformat() for p in selected],
        "cash": [p.cash_price for p in selected],
        "future": [p.future_price for p in selected],
        "gap": [p.gap for p in selected],
        "gap_pct": [p.gap_pct for p in selected],
        "net_profit": [p.net_profit for p in selected],
        "roi_pct": [p.roi_pct for p in selected],
    }
