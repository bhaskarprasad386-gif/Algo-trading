"""Build real-data download requests for historical cash/future backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .cash_future_selection import FutureMode
from .contract_master import ContractMasterCatalog
from .historical_ingest import HistoricalFetchRequest


@dataclass(frozen=True)
class CashFutureDownloadRequest:
    spot: HistoricalFetchRequest
    futures: tuple[HistoricalFetchRequest, ...]

    @property
    def all_requests(self) -> tuple[HistoricalFetchRequest, ...]:
        return (self.spot, *self.futures)


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def build_cash_future_download_request(
    *,
    catalog: ContractMasterCatalog,
    spot_instrument: str,
    exchange: str,
    underlying: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1m",
    mode: FutureMode = FutureMode.BOTH,
    as_of: date | None = None,
) -> CashFutureDownloadRequest:
    """Create source-backed requests from stored historical contract snapshots.

    No current-day contract is substituted for a historical contract. Callers
    should build one request per historical session/as-of date when rolling
    contracts are required across the requested range.
    """
    if end < start:
        raise ValueError("end must not precede start")
    start_ns, end_ns = _ns(start), _ns(end)
    spot = HistoricalFetchRequest("angelone", spot_instrument, timeframe, start_ns, end_ns)
    snapshot_date = as_of or start.date()
    contracts = catalog.resolve(exchange, underlying, snapshot_date, mode.value)
    selected = contracts if isinstance(contracts, tuple) else (contracts,)
    futures = tuple(
        HistoricalFetchRequest(
            "angelone",
            f"{contract.exchange}:{contract.token}:{contract.symbol}",
            timeframe,
            start_ns,
            end_ns,
        )
        for contract in selected
    )
    return CashFutureDownloadRequest(spot=spot, futures=futures)
