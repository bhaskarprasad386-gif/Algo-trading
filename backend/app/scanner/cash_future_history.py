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
