"""Stream durable Cash-Future history into strategy-ready observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from itertools import groupby
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from .contract_master import ContractMasterCatalog, ContractRecord
from .historical_catalog import HistoricalCatalog, HistoricalRecord
from app.scanner.cash_future_history import CashFutureHistoryPoint

MARKET_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class CashFutureHistorySelection:
    spot_instrument: str
    exchange: str
    underlying: str
    start_date: date
    end_date: date
    timeframe: str = "1m"
    contract_month: str | None = None
    mode: str = "CURRENT"
    source: str = "angelone"

    def __post_init__(self) -> None:
        if not self.spot_instrument.strip():
            raise ValueError("spot_instrument is required")
        if not self.exchange.strip() or not self.underlying.strip():
            raise ValueError("exchange and underlying are required")
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if self.mode.upper() not in {"CURRENT", "NEAR"}:
            raise ValueError("mode must be CURRENT or NEAR")
        if not self.source.strip() or not self.timeframe.strip():
            raise ValueError("source and timeframe are required")


def _ns(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MARKET_TZ)
    return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _market_bounds(day: date) -> tuple[int, int]:
    return _ns(datetime.combine(day, time(9, 15), tzinfo=MARKET_TZ)), _ns(datetime.combine(day, time(15, 30), tzinfo=MARKET_TZ))


def _record_price(record: HistoricalRecord) -> float:
    value = record.payload.get("close")
    if value is None:
        raise ValueError(f"historical record has no close price: {record.instrument} @ {record.timestamp_ns}")
    price = float(value)
    if price <= 0:
        raise ValueError(f"historical close price must be positive: {record.instrument} @ {record.timestamp_ns}")
    return price


def _optional_price(payload: dict, key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    price = float(value)
    return price if price > 0 else None


def _optional_quantity(payload: dict, *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            quantity = float(value)
            return quantity if quantity >= 0 else None
    return None


def _datetime_from_ns(timestamp_ns: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).astimezone(MARKET_TZ)


def _in_nse_session(timestamp_ns: int) -> bool:
    local = _datetime_from_ns(timestamp_ns)
    return local.weekday() < 5 and time(9, 15) <= local.time() <= time(15, 30)


def _payload_volume(payload: dict) -> float | None:
    value = payload.get("volume")
    return None if value is None else float(value)


def _payload_oi(payload: dict) -> float | None:
    value = payload.get("open_interest", payload.get("oi"))
    return None if value is None else float(value)


def _merge_pair(
    cash_records: Iterator[HistoricalRecord],
    future_records: Iterator[HistoricalRecord],
    *,
    symbol: str,
    contract: ContractRecord,
) -> Iterator[CashFutureHistoryPoint]:
    cash = next(cash_records, None)
    future = next(future_records, None)
    while cash is not None and future is not None:
        if cash.timestamp_ns < future.timestamp_ns:
            cash = next(cash_records, None)
            continue
        if future.timestamp_ns < cash.timestamp_ns:
            future = next(future_records, None)
            continue
        timestamp_ns = cash.timestamp_ns
        if not _in_nse_session(timestamp_ns):
            cash = next(cash_records, None)
            future = next(future_records, None)
            continue

        cash_payload = dict(cash.payload)
        future_payload = dict(future.payload)
        cash_price = _record_price(cash)
        future_price = _record_price(future)
        gap = future_price - cash_price
        gap_pct = gap / cash_price * 100.0
        margin = float(future_payload.get("margin_required", future_payload.get("margin", 0.0)) or 0.0)
        yield CashFutureHistoryPoint(
            timestamp=_datetime_from_ns(timestamp_ns),
            symbol=symbol,
            contract_month=f"{contract.expiry.year:04d}-{contract.expiry.month:02d}",
            cash_price=cash_price,
            future_price=future_price,
            gap=gap,
            gap_pct=gap_pct,
            lot_size=contract.lot_size,
            margin_required=max(0.0, margin),
            volume=_payload_volume(future_payload),
            oi=_payload_oi(future_payload),
            cash_bid=_optional_price(cash_payload, "bid"),
            cash_ask=_optional_price(cash_payload, "ask"),
            future_bid=_optional_price(future_payload, "bid"),
            future_ask=_optional_price(future_payload, "ask"),
            cash_bid_qty=_optional_quantity(cash_payload, "bid_qty", "bid_quantity", "buy_quantity"),
            cash_ask_qty=_optional_quantity(cash_payload, "ask_qty", "ask_quantity", "sell_quantity"),
            future_bid_qty=_optional_quantity(future_payload, "bid_qty", "bid_quantity", "buy_quantity"),
            future_ask_qty=_optional_quantity(future_payload, "ask_qty", "ask_quantity", "sell_quantity"),
            charges=float(future_payload.get("charges", 0.0) or 0.0),
            funding_cost=float(future_payload.get("funding_cost", 0.0) or 0.0),
            expiry_date=contract.expiry,
        )
        cash = next(cash_records, None)
        future = next(future_records, None)


class CashFutureHistoricalLoader:
    """Resolve historical contracts point-in-time and stream matching cash/future bars."""

    def __init__(self, catalog: HistoricalCatalog, contract_catalog: ContractMasterCatalog) -> None:
        self.catalog = catalog
        self.contract_catalog = contract_catalog

    def _contracts_by_segment(self, selection: CashFutureHistorySelection) -> tuple[tuple[date, date, ContractRecord], ...]:
        days = []
        current = selection.start_date
        while current <= selection.end_date:
            if current.weekday() < 5:
                try:
                    contract = (
                        self.contract_catalog.resolve_contract_month(
                            exchange=selection.exchange,
                            underlying=selection.underlying.upper(),
                            contract_month=selection.contract_month,
                            as_of=current,
                        )
                        if selection.contract_month
                        else self.contract_catalog.resolve(
                            exchange=selection.exchange,
                            underlying=selection.underlying.upper(),
                            as_of=current,
                            mode=selection.mode,
                        )
                    )
                    days.append((current, contract))
                except LookupError:
                    pass
            current = current.fromordinal(current.toordinal() + 1)

        segments: list[tuple[date, date, ContractRecord]] = []
        for _, grouped in groupby(days, key=lambda item: item[1].token):
            block = list(grouped)
            segments.append((block[0][0], block[-1][0], block[0][1]))
        return tuple(segments)

    def iter_points(self, selection: CashFutureHistorySelection) -> Iterable[CashFutureHistoryPoint]:
        """Yield only matched timestamps; no selected history is materialized in RAM."""
        for segment_start, segment_end, contract in self._contracts_by_segment(selection):
            start_ns, _ = _market_bounds(segment_start)
            _, end_ns = _market_bounds(segment_end)
            cash_iter = self.catalog.iter_records(source=selection.source, instrument=selection.spot_instrument, timeframe=selection.timeframe, start_ns=start_ns, end_ns=end_ns)
            future_iter = self.catalog.iter_records(source=selection.source, instrument=f"{contract.exchange}:{contract.token}:{contract.symbol}", timeframe=selection.timeframe, start_ns=start_ns, end_ns=end_ns)
            yield from _merge_pair(cash_iter, future_iter, symbol=selection.underlying.upper(), contract=contract)

    def load_points(self, selection: CashFutureHistorySelection) -> tuple[CashFutureHistoryPoint, ...]:
        """Compatibility helper for small ranges; production runs should use iter_points."""
        return tuple(self.iter_points(selection))


__all__ = ["CashFutureHistorySelection", "CashFutureHistoricalLoader"]
