"""Build deterministic stock and index Cash-Future historical universes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contract_master import ContractMasterCatalog


@dataclass(frozen=True)
class CashFutureUniverseItem:
    underlying: str
    contract_month: str
    future_token: str
    future_symbol: str
    expiry: date
    lot_size: int


@dataclass(frozen=True)
class IndexFutureUniverseItem:
    exchange: str
    underlying: str
    contract_month: str
    future_token: str
    future_symbol: str
    expiry: date
    lot_size: int


@dataclass(frozen=True)
class CashFutureFnoUniverse:
    """Stock cash-future inventory plus a separate index-future inventory.

    Index futures are intentionally not passed through the NSE equity cash-token
    path because an index has no ``*-EQ`` cash instrument.
    """

    stocks: tuple[CashFutureUniverseItem, ...]
    indices: tuple[IndexFutureUniverseItem, ...]

    @property
    def stock_underlyings(self) -> tuple[str, ...]:
        return tuple(sorted({item.underlying for item in self.stocks}))

    @property
    def index_underlyings(self) -> tuple[str, ...]:
        return tuple(sorted({item.underlying for item in self.indices}))


def _effective_date(snapshot_date: date, as_of: date | None) -> date:
    return as_of or snapshot_date


def build_cash_future_stock_universe(
    catalog: ContractMasterCatalog,
    *,
    snapshot_date: date,
    as_of: date | None = None,
) -> tuple[CashFutureUniverseItem, ...]:
    """Enumerate every stock-future expiry represented by one master snapshot."""
    effective_date = _effective_date(snapshot_date, as_of)
    records = catalog.all_contracts(snapshot_date=snapshot_date)
    items: list[CashFutureUniverseItem] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        if record.exchange.upper() != "NFO" or record.instrument_type.upper() != "STOCK_FUTURE":
            continue
        if record.expiry < effective_date:
            continue
        key = (record.underlying.upper(), record.expiry.strftime("%Y-%m"))
        if key in seen:
            continue
        seen.add(key)
        items.append(CashFutureUniverseItem(
            underlying=record.underlying.upper(),
            contract_month=record.expiry.strftime("%Y-%m"),
            future_token=record.token,
            future_symbol=record.symbol,
            expiry=record.expiry,
            lot_size=record.lot_size,
        ))
    return tuple(sorted(items, key=lambda item: (item.underlying, item.expiry, item.future_token)))


def build_cash_future_index_universe(
    catalog: ContractMasterCatalog,
    *,
    snapshot_date: date,
    as_of: date | None = None,
) -> tuple[IndexFutureUniverseItem, ...]:
    """Enumerate index futures separately; never fabricate an index cash token."""
    effective_date = _effective_date(snapshot_date, as_of)
    records = catalog.all_contracts(snapshot_date=snapshot_date)
    items: list[IndexFutureUniverseItem] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        if record.instrument_type.upper() != "INDEX_FUTURE":
            continue
        if record.expiry < effective_date:
            continue
        key = (record.exchange.upper(), record.underlying.upper(), record.expiry.strftime("%Y-%m"))
        if key in seen:
            continue
        seen.add(key)
        items.append(IndexFutureUniverseItem(
            exchange=record.exchange.upper(),
            underlying=record.underlying.upper(),
            contract_month=record.expiry.strftime("%Y-%m"),
            future_token=record.token,
            future_symbol=record.symbol,
            expiry=record.expiry,
            lot_size=record.lot_size,
        ))
    return tuple(sorted(items, key=lambda item: (item.exchange, item.underlying, item.expiry, item.future_token)))


def build_cash_future_fno_universe(
    catalog: ContractMasterCatalog,
    *,
    snapshot_date: date,
    as_of: date | None = None,
) -> CashFutureFnoUniverse:
    """Return all stock F&O futures and all index futures as separate inventories."""
    return CashFutureFnoUniverse(
        stocks=build_cash_future_stock_universe(catalog, snapshot_date=snapshot_date, as_of=as_of),
        indices=build_cash_future_index_universe(catalog, snapshot_date=snapshot_date, as_of=as_of),
    )


__all__ = [
    "CashFutureFnoUniverse",
    "CashFutureUniverseItem",
    "IndexFutureUniverseItem",
    "build_cash_future_fno_universe",
    "build_cash_future_index_universe",
    "build_cash_future_stock_universe",
]
