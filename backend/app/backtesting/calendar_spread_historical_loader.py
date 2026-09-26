"""Point-in-time historical Near/Far contract loader for Calendar Spreads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from itertools import zip_longest
from math import isfinite
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from .contract_master import ContractMasterCatalog, ContractRecord
from .historical_catalog import HistoricalCatalog, HistoricalRecord

MARKET_TZ = ZoneInfo("Asia/Kolkata")
PAIR_TOLERANCE_NS = 60 * 1_000_000_000
LOADER_IDENTITY = "calendar_spread_historical_loader:v1"


@dataclass(frozen=True)
class CalendarSpreadHistoryPoint:
    timestamp: datetime
    underlying: str
    near_expiry: date
    far_expiry: date
    near_bid: float
    near_ask: float
    far_bid: float
    far_ask: float
    lot_size: int
    strike: float | None = None
    option_type: str | None = None


@dataclass(frozen=True)
class CalendarSpreadHistorySelection:
    underlying: str
    exchange: str
    start_date: date
    end_date: date
    near_contract_month: str
    far_contract_month: str
    timeframe: str = "1m"
    source: str = "angelone"
    instrument_type: str = "AUTO"

    def __post_init__(self) -> None:
        if not self.underlying.strip() or not self.exchange.strip():
            raise ValueError("underlying and exchange are required")
        if type(self.start_date) is not date or type(self.end_date) is not date:
            raise TypeError("start_date and end_date must be dates")
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if self.instrument_type.strip().upper() not in {"AUTO", "INDEX_FUTURE", "STOCK_FUTURE", "COMMODITY_FUTURE"}:
            raise ValueError("instrument_type must be AUTO, INDEX_FUTURE, STOCK_FUTURE or COMMODITY_FUTURE")
        if not self.timeframe.strip() or not self.source.strip():
            raise ValueError("timeframe and source are required")
        for value, name in ((self.near_contract_month, "near_contract_month"), (self.far_contract_month, "far_contract_month")):
            if len(value) != 7 or value[4] != "-" or not value[:4].isdigit() or not value[5:].isdigit():
                raise ValueError(f"{name} must be YYYY-MM")
        if self.near_contract_month >= self.far_contract_month:
            raise ValueError("far_contract_month must be later than near_contract_month")


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=MARKET_TZ)
    return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _bounds(day: date, exchange: str) -> tuple[int, int]:
    if exchange.upper() == "MCX":
        return (
            _ns(datetime.combine(day, time(9, 0), tzinfo=MARKET_TZ)),
            _ns(datetime.combine(day, time(23, 30), tzinfo=MARKET_TZ)),
        )
    return (
        _ns(datetime.combine(day, time(9, 15), tzinfo=MARKET_TZ)),
        _ns(datetime.combine(day, time(15, 30), tzinfo=MARKET_TZ)),
    )


def _datetime_from_ns(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ)


def _price(payload: dict, key: str, instrument: str, timestamp_ns: int) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"historical {key} quote missing/non-numeric: {instrument} @ {timestamp_ns}") from exc
    if not isfinite(value) or value <= 0:
        raise ValueError(f"historical {key} quote must be finite and positive: {instrument} @ {timestamp_ns}")
    return value


def _optional_float(payload: dict, key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value > 0 else None


def _pair(near_records: Iterator[HistoricalRecord], far_records: Iterator[HistoricalRecord], *, underlying: str, near: ContractRecord, far: ContractRecord) -> Iterator[CalendarSpreadHistoryPoint]:
    near_row = next(near_records, None)
    far_row = next(far_records, None)
    while near_row is not None and far_row is not None:
        delta = near_row.timestamp_ns - far_row.timestamp_ns
        if abs(delta) > PAIR_TOLERANCE_NS:
            if delta < 0:
                near_row = next(near_records, None)
            else:
                far_row = next(far_records, None)
            continue
        timestamp_ns = max(near_row.timestamp_ns, far_row.timestamp_ns)
        local = _datetime_from_ns(timestamp_ns)
        if local.weekday() >= 5 or (near.exchange.upper() == "MCX" and not (time(9, 0) <= local.time() <= time(23, 30))) or (near.exchange.upper() != "MCX" and not (time(9, 15) <= local.time() <= time(15, 30)):
            if near_row.timestamp_ns <= timestamp_ns:
                near_row = next(near_records, None)
            if far_row.timestamp_ns <= timestamp_ns:
                far_row = next(far_records, None)
            continue
        near_payload, far_payload = dict(near_row.payload), dict(far_row.payload)
        near_bid, near_ask = _price(near_payload, "bid", near_row.instrument, near_row.timestamp_ns), _price(near_payload, "ask", near_row.instrument, near_row.timestamp_ns)
        far_bid, far_ask = _price(far_payload, "bid", far_row.instrument, far_row.timestamp_ns), _price(far_payload, "ask", far_row.instrument, far_row.timestamp_ns)
        if near_ask < near_bid or far_ask < far_bid:
            raise ValueError("historical Calendar Spread bid/ask quote is crossed")
        strike = _optional_float(near_payload, "strike")
        option_type = near_payload.get("option_type")
        if option_type is not None:
            option_type = str(option_type).upper()
            if option_type not in {"CALL", "PUT"}:
                raise ValueError("historical option_type must be CALL or PUT")
        yield CalendarSpreadHistoryPoint(local, underlying, near.expiry, far.expiry, near_bid, near_ask, far_bid, far_ask, near.lot_size, strike, option_type)
        near_row = next(near_records, None)
        far_row = next(far_records, None)


class CalendarSpreadHistoricalLoader:
    """Resolve exact historical Near/Far contracts from the applicable master snapshot."""

    def __init__(self, catalog: HistoricalCatalog, contract_catalog: ContractMasterCatalog) -> None:
        self.catalog = catalog
        self.contract_catalog = contract_catalog

    def _contracts(self, selection: CalendarSpreadHistorySelection, day: date) -> tuple[ContractRecord, ContractRecord]:
        requested_type = selection.instrument_type.strip().upper()
        types = (
            ("INDEX_FUTURE", "NFO"),
            ("INDEX_FUTURE", "BFO"),
            ("STOCK_FUTURE", "NFO"),
            ("STOCK_FUTURE", "BFO"),
            ("COMMODITY_FUTURE", "MCX"),
        ) if requested_type == "AUTO" else ((requested_type, selection.exchange.upper()),)
        errors: list[str] = []
        for instrument_type, default_exchange in types:
            exchange = selection.exchange.upper() if selection.exchange.upper() != "AUTO" else default_exchange
            try:
                near = self.contract_catalog.resolve_contract_month(
                    exchange=exchange, underlying=selection.underlying.upper(),
                    contract_month=selection.near_contract_month, as_of=day,
                    instrument_type=instrument_type,
                )
                far = self.contract_catalog.resolve_contract_month(
                    exchange=exchange, underlying=selection.underlying.upper(),
                    contract_month=selection.far_contract_month, as_of=day,
                    instrument_type=instrument_type,
                )
                if far.expiry <= near.expiry:
                    raise LookupError("historical far contract must expire after near contract")
                if near.lot_size != far.lot_size:
                    raise ValueError("historical Near/Far lot sizes differ")
                return near, far
            except LookupError as exc:
                errors.append(str(exc))
                continue
        raise LookupError(errors[-1] if errors else "no historical Calendar Spread contracts found")

    def iter_points(self, selection: CalendarSpreadHistorySelection) -> Iterable[CalendarSpreadHistoryPoint]:
        day = selection.start_date
        while day <= selection.end_date:
            if day.weekday() < 5:
                try:
                    near, far = self._contracts(selection, day)
                except LookupError:
                    day = date.fromordinal(day.toordinal() + 1)
                    continue
                start_ns, end_ns = _bounds(day, near.exchange)
                near_instrument = f"{near.exchange}:{near.token}:{near.symbol}"
                far_instrument = f"{far.exchange}:{far.token}:{far.symbol}"
                near_records = self.catalog.iter_records(source=selection.source, instrument=near_instrument, timeframe=selection.timeframe, start_ns=start_ns, end_ns=end_ns)
                far_records = self.catalog.iter_records(source=selection.source, instrument=far_instrument, timeframe=selection.timeframe, start_ns=start_ns, end_ns=end_ns)
                yield from _pair(near_records, far_records, underlying=selection.underlying.upper(), near=near, far=far)
            day = date.fromordinal(day.toordinal() + 1)

    def load_points(self, selection: CalendarSpreadHistorySelection) -> tuple[CalendarSpreadHistoryPoint, ...]:
        return tuple(self.iter_points(selection))


__all__ = ["CalendarSpreadHistoryPoint", "CalendarSpreadHistorySelection", "CalendarSpreadHistoricalLoader"]
