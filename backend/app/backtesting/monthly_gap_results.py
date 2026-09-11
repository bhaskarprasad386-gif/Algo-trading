"""Advanced calendar/result helpers for monthly gap search and graph views.

The helpers are data-source agnostic so the calendar, Android client, and web
results UI can share one deterministic contract. No live-order functionality is
introduced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping


@dataclass(frozen=True)
class MonthlyGapRow:
    trading_date: date
    symbol: str
    open_price: float
    high: float
    low: float
    close: float
    lot_size: float
    previous_close: float | None = None
    instrument_type: str = "STOCK"
    contract_month: str | None = None

    @property
    def opening_gap(self) -> float:
        if self.previous_close is None:
            return 0.0
        return self.open_price - self.previous_close

    @property
    def opening_gap_value(self) -> float:
        return abs(self.opening_gap) * self.lot_size

    @property
    def short_gap(self) -> float:
        """Intraday shorting gap: day's high minus day's open."""
        return self.high - self.open_price

    @property
    def short_gap_value(self) -> float:
        return self.short_gap * self.lot_size


@dataclass(frozen=True)
class MonthlyGapSearchResult:
    month: str
    symbol: str
    trading_date: date
    gap: float
    gap_value: float
    open_price: float
    high: float
    low: float
    close: float
    lot_size: float
    instrument_type: str
    contract_month: str | None


@dataclass(frozen=True)
class MonthlyGraphPoint:
    trading_date: date
    open_price: float
    high: float
    low: float
    close: float
    lot_size: float


def build_monthly_rows(rows: Iterable[Mapping[str, object]]) -> tuple[MonthlyGapRow, ...]:
    result: list[MonthlyGapRow] = []
    for row in rows:
        result.append(
            MonthlyGapRow(
                trading_date=_as_date(row["trading_date"]),
                symbol=str(row["symbol"]).strip().upper(),
                open_price=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                lot_size=float(row.get("lot_size", 0) or 0),
                previous_close=_optional_float(row.get("previous_close")),
                instrument_type=str(row.get("instrument_type", "STOCK")).upper(),
                contract_month=(str(row["contract_month"]) if row.get("contract_month") is not None else None),
            )
        )
    return tuple(result)


def search_monthly_largest_gap(
    rows: Iterable[MonthlyGapRow],
    *,
    year: int,
    month: int,
    mode: str = "opening",
    symbol: str | None = None,
    instrument_type: str | None = None,
    contract_month: str | None = None,
) -> MonthlyGapSearchResult | None:
    """Return the largest monthly gap using deterministic lot-weighted ranking.

    ``opening`` ranks abs(open - previous close) x lot. ``shorting`` ranks
    (high - open) x lot, matching the calendar's high-based shorting definition.
    """
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    if mode not in {"opening", "shorting"}:
        raise ValueError("mode must be opening or shorting")
    month_key = f"{year:04d}-{month:02d}"
    candidates = []
    wanted_symbol = symbol.strip().upper() if symbol else None
    wanted_type = instrument_type.strip().upper() if instrument_type else None
    for row in rows:
        if row.trading_date.strftime("%Y-%m") != month_key:
            continue
        if wanted_symbol and row.symbol != wanted_symbol:
            continue
        if wanted_type and row.instrument_type != wanted_type:
            continue
        if contract_month and row.contract_month != contract_month:
            continue
        value = row.opening_gap_value if mode == "opening" else row.short_gap_value
        if mode == "opening" and row.previous_close is None:
            continue
        candidates.append((value, row))
    if not candidates:
        return None
    _, top = max(candidates, key=lambda item: (item[0], item[1].trading_date, item[1].symbol))
    gap = top.opening_gap if mode == "opening" else top.short_gap
    value = top.opening_gap_value if mode == "opening" else top.short_gap_value
    return MonthlyGapSearchResult(
        month=month_key,
        symbol=top.symbol,
        trading_date=top.trading_date,
        gap=gap,
        gap_value=value,
        open_price=top.open_price,
        high=top.high,
        low=top.low,
        close=top.close,
        lot_size=top.lot_size,
        instrument_type=top.instrument_type,
        contract_month=top.contract_month,
    )


def monthly_graph(rows: Iterable[MonthlyGapRow], *, year: int, month: int, symbol: str, contract_month: str | None = None) -> tuple[MonthlyGraphPoint, ...]:
    """Return one OHLC point per trading day for a symbol in a month."""
    wanted = symbol.strip().upper()
    points = [
        MonthlyGraphPoint(r.trading_date, r.open_price, r.high, r.low, r.close, r.lot_size)
        for r in rows
        if r.symbol == wanted
        and r.trading_date.year == year
        and r.trading_date.month == month
        and (contract_month is None or r.contract_month == contract_month)
    ]
    return tuple(sorted(points, key=lambda p: p.trading_date))


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


__all__ = [
    "MonthlyGapRow",
    "MonthlyGapSearchResult",
    "MonthlyGraphPoint",
    "build_monthly_rows",
    "search_monthly_largest_gap",
    "monthly_graph",
]
