"""Convert genuine historical chain contracts into adapter-ready quote payloads.

The bridge is intentionally strict: CE and PE for an option strike must come
from the same historical timestamp/expiry/underlying, and no missing side is
fabricated. Futures retain their real expiry and liquidity fields.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Mapping, Sequence

from .arbitrage_backtester import FutureQuote, OptionQuote
from .arbitrage_chain_selector import ChainContract


def option_quote(contract_pair: Sequence[ChainContract]) -> Mapping[str, object]:
    """Build one adapter OptionQuote mapping from the real CE+PE contracts."""
    if len(contract_pair) != 2:
        raise ValueError("exactly one CE and one PE contract are required")
    by_type = {contract.option_type: contract for contract in contract_pair}
    if set(by_type) != {"CE", "PE"}:
        raise ValueError("an option quote requires both CE and PE contracts")
    call = by_type["CE"]
    put = by_type["PE"]
    identity = (call.timestamp_ns, call.venue, call.underlying, call.expiry,
                call.strike, call.lot_size, call.instrument_class)
    other = (put.timestamp_ns, put.venue, put.underlying, put.expiry,
             put.strike, put.lot_size, put.instrument_class)
    if identity != other:
        raise ValueError("CE and PE must share timestamp, venue, underlying, expiry, strike, lot and class")
    quote = OptionQuote(
        timestamp_ns=call.timestamp_ns,
        underlying=call.underlying,
        expiry=call.expiry,
        strike=call.strike,
        call_bid=call.bid,
        call_ask=call.ask,
        put_bid=put.bid,
        put_ask=put.ask,
        lot_size=call.lot_size,
        instrument_class=call.instrument_class,
    )
    return asdict(quote)


def option_quotes(contracts: Sequence[ChainContract]) -> tuple[Mapping[str, object], ...]:
    """Group genuine CE/PE contracts by strike without creating missing legs."""
    groups: dict[tuple[int, str, str, int, float], dict[str, ChainContract]] = {}
    for contract in contracts:
        key = (contract.timestamp_ns, contract.venue, contract.underlying,
               contract.expiry, contract.strike)
        groups.setdefault(key, {})[contract.option_type] = contract
    quotes: list[Mapping[str, object]] = []
    for key in sorted(groups):
        sides = groups[key]
        if set(sides) != {"CE", "PE"}:
            continue
        quotes.append(option_quote((sides["CE"], sides["PE"])))
    return tuple(quotes)


def future_quote(contract: Mapping[str, object]) -> Mapping[str, object]:
    """Normalize a real future contract mapping into the adapter FutureQuote shape."""
    required = ("timestamp_ns", "underlying", "expiry", "bid", "ask", "lot_size",
                "instrument_class", "volume", "oi")
    missing = [key for key in required if key not in contract]
    if missing:
        raise ValueError(f"future contract missing required fields: {', '.join(missing)}")
    quote = FutureQuote(
        timestamp_ns=int(contract["timestamp_ns"]),
        underlying=str(contract["underlying"]),
        expiry=int(contract["expiry"]),
        bid=float(contract["bid"]),
        ask=float(contract["ask"]),
        lot_size=int(contract["lot_size"]),
        instrument_class=str(contract["instrument_class"]),
        volume=int(contract["volume"]),
        oi=int(contract["oi"]),
    )
    return asdict(quote)


__all__ = ["option_quote", "option_quotes", "future_quote"]
