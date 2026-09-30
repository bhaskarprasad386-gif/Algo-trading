"""Dynamic Angel One commodity contract selection for the shared market-data layer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Any

from app.market_data.contract_resolver import DynamicContractResolver, ContractCandidate
from dataclasses import dataclass


@dataclass(frozen=True)
class CommoditySubscription:
    exchange_type: int
    token: str
    symbol: str
    underlying: str
    expiry: str | None = None
    option_type: str | None = None
    strike: float | None = None
    lot_size: int | None = None

_EXCHANGE_TYPES = {"MCX": 5, "NCDEX": 7}


@dataclass(frozen=True)
class CommodityContractSelection:
    subscriptions: tuple[CommoditySubscription, ...]
    underlying: str
    futures: tuple[ContractCandidate, ...]
    option_count: int


def select_commodity_contracts(
    rows: Iterable[Mapping[str, Any]],
    *,
    underlying: str,
    atm_strike: float | None = None,
    strike_count: int = 10,
) -> CommodityContractSelection:
    """Resolve current/near futures and ATM±N actual option positions.

    Only contracts present in the supplied Angel One master are returned.
    """
    symbol = str(underlying).strip().upper()
    if not symbol:
        raise ValueError("underlying is required")
    if isinstance(strike_count, bool) or strike_count < 0:
        raise ValueError("strike_count must be non-negative")
    materialized = tuple(dict(r) for r in rows)
    resolver = DynamicContractResolver(materialized, as_of=date.today())
    futures = resolver.resolve_futures(symbol, limit=2)
    if not futures:
        raise LookupError(f"no active commodity futures found for {symbol}")
    exchange = futures[0].segment.strip().upper()
    exchange_type = _EXCHANGE_TYPES.get(exchange)
    if exchange_type is None:
        raise LookupError(f"unsupported commodity exchange for {symbol}: {exchange}")

    selected = []
    seen: set[tuple[int, str]] = set()
    for future in futures:
        key = (exchange_type, future.token)
        if key in seen:
            continue
        seen.add(key)
        selected.append(CommoditySubscription(
            exchange_type, future.token, future.symbol, symbol, "COMMODITY",
            future.expiry, None, None, future.lot_size
        ))

    option_count = 0
    if atm_strike is not None:
        options = resolver.resolve_options(
            symbol, float(atm_strike), strike_count=strike_count, expiry_rank=0
        )
        for option in options:
            key = (exchange_type, option.token)
            if key in seen:
                continue
            seen.add(key)
            selected.append(SyntheticSubscription(
                exchange_type, option.token, option.symbol, symbol, "COMMODITY",
                option.expiry, option.option_type, option.strike, option.lot_size
            ))
            option_count += 1

    return CommodityContractSelection(
        subscriptions=tuple(selected),
        underlying=symbol,
        futures=futures,
        option_count=option_count,
    )


__all__ = ["CommodityContractSelection", "select_commodity_contracts"]
