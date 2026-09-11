"""Daily opening-gap ranking for the backtesting calendar.

The calendar uses the previous trading day's close as the reference and ranks
F&O stocks by absolute opening gap multiplied by the historical lot size.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping


@dataclass(frozen=True)
class DailyGapObservation:
    trading_date: date
    symbol: str
    previous_close: float
    open_price: float
    high: float
    low: float
    close: float
    lot_size: float

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise ValueError("symbol is required")
        for value, name in (
            (self.previous_close, "previous_close"),
            (self.open_price, "open_price"),
            (self.high, "high"),
            (self.low, "low"),
            (self.close, "close"),
            (self.lot_size, "lot_size"),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.high < max(self.open_price, self.close):
            raise ValueError("high must be >= open_price and close")
        if self.low > min(self.open_price, self.close):
            raise ValueError("low must be <= open_price and close")

    @property
    def gap(self) -> float:
        """Opening gap in price points: today's open minus previous close."""
        return self.open_price - self.previous_close

    @property
    def gap_percent(self) -> float:
        return (self.gap / self.previous_close) * 100.0

    @property
    def weighted_gap(self) -> float:
        """Absolute gap value for one futures lot."""
        return abs(self.gap) * self.lot_size

    @property
    def direction(self) -> str:
        if self.gap > 0:
            return "UP"
        if self.gap < 0:
            return "DOWN"
        return "FLAT"


@dataclass(frozen=True)
class TopDailyGap:
    trading_date: date
    symbol: str
    direction: str
    gap: float
    gap_percent: float
    weighted_gap: float
    previous_close: float
    open_price: float
    high: float
    low: float
    close: float
    lot_size: float


def build_daily_gap_observations(
    rows: Iterable[Mapping[str, object]],
    *,
    date_field: str = "trading_date",
    symbol_field: str = "symbol",
    previous_close_field: str = "previous_close",
    open_field: str = "open",
    high_field: str = "high",
    low_field: str = "low",
    close_field: str = "close",
    lot_size_field: str = "lot_size",
) -> tuple[DailyGapObservation, ...]:
    return tuple(
        DailyGapObservation(
            trading_date=_as_date(row[date_field]),
            symbol=str(row[symbol_field]),
            previous_close=float(row[previous_close_field]),
            open_price=float(row[open_field]),
            high=float(row[high_field]),
            low=float(row[low_field]),
            close=float(row[close_field]),
            lot_size=float(row[lot_size_field]),
        )
        for row in rows
    )


def top_daily_gap(
    observations: Iterable[DailyGapObservation],
    trading_date: date,
) -> TopDailyGap | None:
    """Return the stock with the largest absolute gap x lot-size on a date."""
    candidates = (row for row in observations if row.trading_date == trading_date)
    top = max(candidates, key=lambda row: (row.weighted_gap, row.symbol), default=None)
    if top is None:
        return None
    return TopDailyGap(
        trading_date=top.trading_date,
        symbol=top.symbol,
        direction=top.direction,
        gap=top.gap,
        gap_percent=top.gap_percent,
        weighted_gap=top.weighted_gap,
        previous_close=top.previous_close,
        open_price=top.open_price,
        high=top.high,
        low=top.low,
        close=top.close,
        lot_size=top.lot_size,
    )


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = [
    "DailyGapObservation",
    "TopDailyGap",
    "build_daily_gap_observations",
    "top_daily_gap",
]
