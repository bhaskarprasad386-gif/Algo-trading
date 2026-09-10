"""Angel One-backed synchronized Cash-Future historical source."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.backtesting.angelone_historical import AngelOneHistoricalSource
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_cash_token_resolver import HistoricalCashTokenResolver
from app.backtesting.historical_contract_resolver import HistoricalContractResolver
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.scanner.cash_future_history import CashFutureHistoryPoint

IST = ZoneInfo("Asia/Kolkata")


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _day_windows(start: datetime, end: datetime) -> Iterable[tuple[datetime, datetime]]:
    current = start.astimezone(IST).date()
    final = end.astimezone(IST).date()
    while current <= final:
        day_start = datetime.combine(current, time(9, 15), tzinfo=IST)
        day_end = datetime.combine(current, time(15, 30), tzinfo=IST)
        yield max(start, day_start), min(end, day_end)
        current = current.fromordinal(current.toordinal() + 1)


def _rows_by_timestamp(rows: Iterable[Any]) -> dict[int, Any]:
    return {row.timestamp_ns: row for row in rows}


class AngelOneCashFutureHistoricalSource:
    """Fetch real NSE cash + exact NFO future candles and synchronize timestamps."""

    def __init__(
        self,
        *,
        master_rows: Iterable[Mapping[str, Any]],
        contract_catalog: ContractMasterCatalog,
        historical_source: AngelOneHistoricalSource | None = None,
    ) -> None:
        rows = tuple(master_rows)
        self.cash_resolver = HistoricalCashTokenResolver(rows)
        self.contract_resolver = HistoricalContractResolver(contract_catalog)
        self.historical_source = historical_source or AngelOneHistoricalSource()

    def fetch(
        self,
        *,
        symbol: str,
        contract_month: str,
        start: datetime,
        end: datetime,
    ) -> Iterable[CashFutureHistoryPoint]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if end < start:
            raise ValueError("end must not be before start")

        cash = self.cash_resolver.resolve(symbol)
        contract = self.contract_resolver.resolve_future(
            underlying=symbol,
            contract_month=contract_month,
            as_of=start.astimezone(IST).date(),
        )

        for day_start, day_end in _day_windows(start, end):
            if day_start > day_end:
                continue
            cash_request = HistoricalFetchRequest(
                source="angelone", instrument=cash.instrument, timeframe="1m",
                start_ns=_ns(day_start), end_ns=_ns(day_end),
            )
            future_request = HistoricalFetchRequest(
                source="angelone", instrument=f"NFO:{contract.token}:{contract.record.symbol}", timeframe="1m",
                start_ns=_ns(day_start), end_ns=_ns(day_end),
            )
            cash_rows = _rows_by_timestamp(self.historical_source.fetch(cash_request))
            future_rows = _rows_by_timestamp(self.historical_source.fetch(future_request))

            for timestamp_ns in sorted(cash_rows.keys() & future_rows.keys()):
                cash_row = cash_rows[timestamp_ns]
                future_row = future_rows[timestamp_ns]
                cash_price = float(cash_row.payload["close"])
                future_price = float(future_row.payload["close"])
                gap = future_price - cash_price
                gap_pct = gap / cash_price * 100.0 if cash_price else 0.0
                yield CashFutureHistoryPoint(
                    timestamp=datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).astimezone(IST),
                    symbol=symbol, contract_month=contract_month,
                    cash_price=cash_price, future_price=future_price,
                    gap=gap, gap_pct=gap_pct,
                    lot_size=contract.record.lot_size, margin_required=0.0,
                    volume=float(future_row.payload["volume"]) if future_row.payload.get("volume") is not None else None,
                    oi=float(future_row.payload["open_interest"]) if future_row.payload.get("open_interest") is not None else None,
                    expiry_date=contract.record.expiry,
                )


__all__ = ["AngelOneCashFutureHistoricalSource"]
