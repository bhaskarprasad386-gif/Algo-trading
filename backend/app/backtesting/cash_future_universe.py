"""Build a deterministic Cash-Future historical acquisition universe."""

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


def build_cash_future_stock_universe(
    catalog: ContractMasterCatalog,
    *,
    snapshot_date: date,
    as_of: date | None = None,
) -> tuple[CashFutureUniverseItem, ...]:
    """Enumerate every stock-future expiry represented by one master snapshot.

    The snapshot is the source of truth for the historical token and lot size.
    Expired contracts are excluded when ``as_of`` is supplied, while all future
    expiry months remain separate so rollover data is never mixed implicitly.
    """
    effective_date = as_of or snapshot_date
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
        items.append(
            CashFutureUniverseItem(
                underlying=record.underlying.upper(),
                contract_month=record.expiry.strftime("%Y-%m"),
                future_token=record.token,
                future_symbol=record.symbol,
                expiry=record.expiry,
                lot_size=record.lot_size,
            )
        )
    return tuple(sorted(items, key=lambda item: (item.underlying, item.expiry, item.future_token)))


__all__ = ["CashFutureUniverseItem", "build_cash_future_stock_universe"]
