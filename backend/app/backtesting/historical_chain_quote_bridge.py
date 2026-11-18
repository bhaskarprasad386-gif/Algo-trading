"""Convert genuine historical chain contracts into adapter-ready quote payloads."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .arbitrage_backtester import FutureQuote, OptionQuote
from .arbitrage_chain_selector import ChainContract


def _chain_contract(value: Mapping[str, Any]) -> ChainContract:
    required = ("timestamp_ns", "venue", "underlying", "instrument_class", "expiry", "strike", "option_type", "lot_size", "volume", "oi", "bid", "ask")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"chain contract missing required fields: {', '.join(missing)}")
    return ChainContract(
        timestamp_ns=int(value["timestamp_ns"]), venue=str(value["venue"]), underlying=str(value["underlying"]),
        instrument_class=str(value["instrument_class"]), expiry=int(value["expiry"]), strike=float(value["strike"]),
        option_type=str(value["option_type"]), lot_size=int(value["lot_size"]), volume=int(value["volume"]),
        oi=int(value["oi"]), bid=float(value["bid"]), ask=float(value["ask"]),
    )


def option_quote(contract_pair: Sequence[ChainContract]) -> Mapping[str, object]:
    """Build one adapter OptionQuote mapping from the real CE+PE contracts."""
    if len(contract_pair) != 2:
        raise ValueError("exactly one CE and one PE contract are required")
    by_type = {contract.option_type: contract for contract in contract_pair}
    if set(by_type) != {"CE", "PE"}:
        raise ValueError("an option quote requires both CE and PE contracts")
    call, put = by_type["CE"], by_type["PE"]
    identity = (call.timestamp_ns, call.venue, call.underlying, call.expiry, call.strike, call.lot_size, call.instrument_class)
    other = (put.timestamp_ns, put.venue, put.underlying, put.expiry, put.strike, put.lot_size, put.instrument_class)
    if identity != other:
        raise ValueError("CE and PE must share timestamp, venue, underlying, expiry, strike, lot and class")
    return asdict(OptionQuote(
        timestamp_ns=call.timestamp_ns, underlying=call.underlying, expiry=call.expiry, strike=call.strike,
        call_bid=call.bid, call_ask=call.ask, put_bid=put.bid, put_ask=put.ask,
        lot_size=call.lot_size, instrument_class=call.instrument_class,
    ))


def option_quotes(contracts: Sequence[ChainContract]) -> tuple[Mapping[str, object], ...]:
    """Group genuine CE/PE contracts by strike without creating missing legs."""
    groups: dict[tuple[int, str, str, int, float], dict[str, ChainContract]] = {}
    for contract in contracts:
        key = (contract.timestamp_ns, contract.venue, contract.underlying, contract.expiry, contract.strike)
        groups.setdefault(key, {})[contract.option_type] = contract
    return tuple(option_quote((sides["CE"], sides["PE"])) for key in sorted(groups) if set((sides := groups[key])) == {"CE", "PE"})


def future_quote(contract: Mapping[str, object]) -> Mapping[str, object]:
    """Normalize a real future contract mapping into the adapter FutureQuote shape."""
    required = ("timestamp_ns", "underlying", "expiry", "bid", "ask", "lot_size", "instrument_class", "volume", "oi")
    missing = [key for key in required if key not in contract]
    if missing:
        raise ValueError(f"future contract missing required fields: {', '.join(missing)}")
    return asdict(FutureQuote(
        timestamp_ns=int(contract["timestamp_ns"]), underlying=str(contract["underlying"]), expiry=int(contract["expiry"]),
        bid=float(contract["bid"]), ask=float(contract["ask"]), lot_size=int(contract["lot_size"]),
        instrument_class=str(contract["instrument_class"]), volume=int(contract["volume"]), oi=int(contract["oi"]),
    ))


def _normalize_option_contracts(value: Any) -> Mapping[str, object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("option contracts must be a CE/PE sequence")
    contracts = tuple(_chain_contract(item) for item in value)
    return option_quote(contracts)


def normalize_chain_payload(value: Any, *, option: bool) -> Mapping[str, object]:
    """Normalize raw catalog chain payloads; already-normalized payloads pass through."""
    if isinstance(value, Mapping):
        if option and "call_bid" in value and "put_bid" in value:
            return dict(value)
        if not option and "future_bid" in value and "future_ask" in value:
            return dict(value)
        if option and "contracts" in value:
            return _normalize_option_contracts(value["contracts"])
        if not option and "contract" in value:
            return future_quote(value["contract"])
        if not option and {"bid", "ask", "volume", "oi"}.issubset(value):
            return future_quote(value)
    if option:
        return _normalize_option_contracts(value)
    raise ValueError("unsupported historical chain payload")


__all__ = ["option_quote", "option_quotes", "future_quote", "normalize_chain_payload"]
