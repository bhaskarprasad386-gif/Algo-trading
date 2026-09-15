"""Historical Cash-Future matching and graph-series helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _normalize_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(IST).replace(tzinfo=None)


def _consistent(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)


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
    cash_bid_qty: float | None = None
    cash_ask_qty: float | None = None
    future_bid_qty: float | None = None
    future_ask_qty: float | None = None
    charges: float = 0.0
    funding_cost: float = 0.0
    net_profit: float = 0.0
    roi_pct: float = 0.0
    expiry_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _normalize_timestamp(self.timestamp))
        if not self.symbol or not self.contract_month:
            raise ValueError("symbol and contract_month are required")
        for value, name in (
            (self.cash_price, "cash_price"), (self.future_price, "future_price"),
            (self.gap, "gap"), (self.gap_pct, "gap_pct"),
            (self.margin_required, "margin_required"), (self.charges, "charges"),
            (self.funding_cost, "funding_cost"), (self.net_profit, "net_profit"),
            (self.roi_pct, "roi_pct"),
        ):
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.cash_price <= 0 or self.future_price <= 0:
            raise ValueError("cash_price and future_price must be positive")
        if type(self.lot_size) is not int or self.lot_size <= 0:
            raise ValueError("lot_size must be a positive integer")
        if self.margin_required < 0 or self.charges < 0 or self.funding_cost < 0:
            raise ValueError("margin_required, charges and funding_cost must be non-negative")
        expected_gap = self.future_price - self.cash_price
        expected_gap_pct = expected_gap / self.cash_price * 100.0
        if not _consistent(self.gap, expected_gap):
            raise ValueError("gap must equal future_price - cash_price")
        if not _consistent(self.gap_pct, expected_gap_pct):
            raise ValueError("gap_pct must equal gap / cash_price * 100")
        if self.expiry_date is not None and self.expiry_date < self.timestamp.date():
            raise ValueError("expiry_date cannot precede observation date")
        for value, name in (
            (self.volume, "volume"), (self.oi, "oi"), (self.cash_bid, "cash_bid"),
            (self.cash_ask, "cash_ask"), (self.future_bid, "future_bid"),
            (self.future_ask, "future_ask"), (self.cash_bid_qty, "cash_bid_qty"),
            (self.cash_ask_qty, "cash_ask_qty"), (self.future_bid_qty, "future_bid_qty"),
            (self.future_ask_qty, "future_ask_qty"),
        ):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite when provided")


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
    if not math.isfinite(float(target_gap)) or not math.isfinite(float(tolerance)):
        raise ValueError("target_gap and tolerance must be finite")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    lower_bound = target_gap - tolerance
    upper_bound = target_gap + tolerance
    matches = []
    for point in points:
        if contract_month is not None and point.contract_month != contract_month:
            continue
        if point.gap < lower_bound or point.gap > upper_bound:
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
    """Find prior gap occurrences and measure the first subsequent exit."""
    if max_holding_days <= 0:
        raise ValueError("max_holding_days must be positive")
    if not math.isfinite(float(charges_per_trade)) or not math.isfinite(float(funding_cost_per_trade)):
        raise ValueError("charges_per_trade and funding_cost_per_trade must be finite")
    if charges_per_trade < 0 or funding_cost_per_trade < 0:
        raise ValueError("charges_per_trade and funding_cost_per_trade must be non-negative")
    if not math.isfinite(float(exit_gap)):
        raise ValueError("exit_gap must be finite")
    ordered = sorted(points, key=lambda p: p.timestamp)
    matches = find_historical_gap_matches(ordered, target_gap, tolerance, contract_month)
    outcomes: list[HistoricalGapOutcome] = []

    for match in matches:
        entry_point = next(
            p for p in ordered
            if p.timestamp == match.timestamp
            and p.symbol == match.symbol
            and p.contract_month == match.contract_month
        )
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
        gross = (match.gap - exit_point.gap) * entry_point.lot_size
        net = gross - charges_per_trade - funding_cost_per_trade
        capital = entry_point.cash_price * entry_point.lot_size + entry_point.margin_required
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("entry capital must be finite and positive")
        if not math.isfinite(net):
            raise ValueError("historical outcome P&L must be finite")
        roi = net / capital * 100.0
        if not math.isfinite(roi):
            raise ValueError("historical outcome ROI must be finite")
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
