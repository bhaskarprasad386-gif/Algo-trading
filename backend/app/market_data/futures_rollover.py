"""Deterministic futures contract-chain selection for historical backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from app.market_data.historical_backtest_sync import HistoricalBacktestInstrument
from app.market_data.session_calendar import NSE_SESSION_CLOSE, NSE_SESSION_OPEN


@dataclass(frozen=True)
class FuturesContract:
    symbol: str
    token: str
    exchange: str
    segment: str
    contract_month: str
    expiry_date: date
    lot_size: int | None = None

    @property
    def instrument(self) -> HistoricalBacktestInstrument:
        return HistoricalBacktestInstrument(
            symbol=self.symbol,
            token=self.token,
            exchange=self.exchange,
            segment=self.segment,
            instrument_type="FUTURE",
            contract_month=self.contract_month,
            expiry_date=self.expiry_date,
            lot_size=self.lot_size,
        )


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.upper(), fmt).date()
        except ValueError:
            continue
    return None


def _is_future_row(row: dict[str, Any], underlying: str) -> bool:
    symbol = str(row.get("symbol") or row.get("name") or "").upper()
    segment = str(row.get("exch_seg") or row.get("exchange") or "").upper()
    instrument_type = str(row.get("instrumenttype") or row.get("instrument_type") or "").upper()
    return (
        segment == "NFO"
        and underlying.upper() in symbol
        and instrument_type in {"FUTSTK", "FUTIDX", "FUTURE"}
        and _parse_date(row.get("expiry")) is not None
        and str(row.get("token") or "").strip()
    )


def build_futures_chain(rows: Iterable[dict[str, Any]], *, underlying: str) -> list[FuturesContract]:
    """Build a chronological, deduplicated NFO futures chain from Angel master rows."""
    candidates: list[FuturesContract] = []
    seen: set[tuple[str, date]] = set()
    for row in rows:
        if not _is_future_row(row, underlying):
            continue
        expiry = _parse_date(row.get("expiry"))
        assert expiry is not None
        symbol = str(row.get("symbol") or row.get("name") or underlying).strip()
        token = str(row.get("token")).strip()
        key = (symbol.upper(), expiry)
        if key in seen:
            continue
        seen.add(key)
        contract_month = str(row.get("contract_month") or expiry.strftime("%Y-%m"))
        lot_raw = row.get("lotsize") if row.get("lotsize") is not None else row.get("lot_size")
        lot_size = int(lot_raw) if lot_raw not in (None, "") else None
        candidates.append(
            FuturesContract(symbol, token, "NFO", "NFO", contract_month, expiry, lot_size)
        )
    return sorted(candidates, key=lambda contract: (contract.expiry_date, contract.symbol, contract.token))


def select_contract(chain: list[FuturesContract], timestamp: datetime) -> FuturesContract | None:
    """Select the front eligible contract at a timestamp; never use an expired contract."""
    eligible = [contract for contract in chain if contract.expiry_date >= timestamp.date()]
    return eligible[0] if eligible else None


def _next_trading_session_open(expiry: date) -> datetime:
    """Return the next weekday session open after a contract expiry date."""
    day = expiry + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, NSE_SESSION_OPEN)


def map_rollover(chain: list[FuturesContract], start: datetime, end: datetime) -> list[tuple[datetime, datetime, FuturesContract]]:
    """Map contracts to non-overlapping windows, keeping expiry-day trading with the expiring contract."""
    if start >= end:
        raise ValueError("rollover start must be before end")
    selected = [contract for contract in chain if contract.expiry_date >= start.date()]
    if not selected:
        return []
    windows: list[tuple[datetime, datetime, FuturesContract]] = []
    cursor = start
    for index, contract in enumerate(selected):
        if cursor >= end:
            break
        if index < len(selected) - 1:
            next_boundary = _next_trading_session_open(contract.expiry_date)
            window_end = min(end, next_boundary)
        else:
            window_end = end
        if cursor < window_end:
            windows.append((cursor, window_end, contract))
        cursor = window_end
    return windows
