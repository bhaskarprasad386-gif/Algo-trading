"""Point-in-time arbitrage universe, expiry and full-chain discovery.

The discovery layer consumes the provider's concrete instrument master plus
point-in-time quote/OI/volume observations. It never invents a strike, expiry,
token, quote or missing CE/PE leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from .arbitrage_chain_selector import ChainContract


_OPTION_TYPES = {"OPTSTK": "STOCK", "OPTIDX": "INDEX"}
_FUTURE_TYPES = {"FUTSTK": "STOCK", "FUTIDX": "INDEX"}
_SUPPORTED_SEGMENTS = {"NFO", "BFO"}
_VENUE_BY_SEGMENT = {"NFO": "NSE", "BFO": "BSE"}


def _expiry(value: Any) -> date:
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid expiry: {value!r}")


def _strike(value: Any) -> float:
    # Angel One equity/index instrument-master strikes are represented in paise.
    number = float(value)
    if number <= 0:
        raise ValueError("option strike must be positive")
    return number / 100.0


def _option_side(symbol: Any) -> str:
    text = str(symbol or "").strip().upper()
    if text.endswith("CE"):
        return "CE"
    if text.endswith("PE"):
        return "PE"
    raise ValueError(f"option symbol has no CE/PE suffix: {text!r}")


@dataclass(frozen=True)
class DiscoveredUniverse:
    stocks: tuple[str, ...]
    indexes: tuple[str, ...]
    expiries: tuple[date, ...]


@dataclass(frozen=True)
class ChainDiscoveryResult:
    underlying: str
    instrument_class: str
    exchange: str
    expiry: date
    contracts: tuple[ChainContract, ...]


class ArbitrageInstrumentDiscovery:
    """Discover concrete F&O universes and point-in-time option chains."""

    @staticmethod
    def discover_universe(
        rows: Iterable[Mapping[str, Any]],
        *,
        as_of: date,
        nifty50_symbols: Iterable[str] = (),
        exchanges: Iterable[str] = _SUPPORTED_SEGMENTS,
    ) -> DiscoveredUniverse:
        allowed_exchanges = {str(x).strip().upper() for x in exchanges}
        allowed_stocks = {str(x).strip().upper() for x in nifty50_symbols if str(x).strip()}
        stock = set()
        index = set()
        expiries: set[date] = set()
        for row in rows:
            segment = str(row.get("exch_seg", "")).strip().upper()
            typ = str(row.get("instrumenttype", "")).strip().upper()
            name = str(row.get("name", "")).strip().upper()
            if segment not in allowed_exchanges or not name or typ not in (_OPTION_TYPES | _FUTURE_TYPES):
                continue
            try:
                expiry = _expiry(row.get("expiry"))
            except ValueError:
                continue
            if expiry < as_of:
                continue
            cls = (_OPTION_TYPES | _FUTURE_TYPES)[typ]
            if cls == "STOCK":
                if allowed_stocks and name not in allowed_stocks:
                    continue
                stock.add(name)
            else:
                index.add(name)
            expiries.add(expiry)
        return DiscoveredUniverse(tuple(sorted(stock)), tuple(sorted(index)), tuple(sorted(expiries)))

    @staticmethod
    def discover_expiries(
        rows: Iterable[Mapping[str, Any]],
        *,
        underlying: str,
        instrument_class: str,
        as_of: date,
        exchange: str,
    ) -> tuple[date, ...]:
        cls = instrument_class.strip().upper()
        typ = "OPTSTK" if cls == "STOCK" else "OPTIDX" if cls == "INDEX" else ""
        if not typ:
            raise ValueError("instrument_class must be STOCK or INDEX")
        result = set()
        for row in rows:
            if str(row.get("exch_seg", "")).strip().upper() != exchange.strip().upper():
                continue
            if str(row.get("instrumenttype", "")).strip().upper() != typ:
                continue
            if str(row.get("name", "")).strip().upper() != underlying.strip().upper():
                continue
            try:
                expiry = _expiry(row.get("expiry"))
            except ValueError:
                continue
            if expiry >= as_of:
                result.add(expiry)
        return tuple(sorted(result))

    @staticmethod
    def enumerate_chain(
        rows: Iterable[Mapping[str, Any]],
        *,
        underlying: str,
        instrument_class: str,
        exchange: str,
        expiry: date,
        timestamp_ns: int,
        quotes_by_token: Mapping[str, Mapping[str, Any]],
    ) -> ChainDiscoveryResult:
        cls = instrument_class.strip().upper()
        typ = "OPTSTK" if cls == "STOCK" else "OPTIDX" if cls == "INDEX" else ""
        if not typ:
            raise ValueError("instrument_class must be STOCK or INDEX")
        contracts: list[ChainContract] = []
        seen: set[tuple[str, float, str]] = set()
        for row in rows:
            if str(row.get("exch_seg", "")).strip().upper() != exchange.strip().upper():
                continue
            if str(row.get("instrumenttype", "")).strip().upper() != typ:
                continue
            if str(row.get("name", "")).strip().upper() != underlying.strip().upper():
                continue
            try:
                row_expiry = _expiry(row.get("expiry"))
                strike = _strike(row.get("strike"))
                side = _option_side(row.get("symbol"))
                lot_size = int(str(row.get("lotsize", "0")).strip())
            except (TypeError, ValueError):
                continue
            if row_expiry != expiry or lot_size <= 0:
                continue
            token = str(row.get("token", "")).strip()
            if not token:
                continue
            key = (token, strike, side)
            if key in seen:
                raise ValueError(f"duplicate option contract identity: {key}")
            seen.add(key)
            quote = quotes_by_token.get(token)
            if quote is None:
                raise ValueError(f"missing point-in-time quote for token {token}")
            quote_ts = quote.get("timestamp_ns")
            if quote_ts != timestamp_ns:
                raise ValueError(f"quote timestamp mismatch for token {token}")
            bid = float(quote.get("bid", 0))
            ask = float(quote.get("ask", 0))
            volume = float(quote.get("volume", 0))
            oi = float(quote.get("oi", 0))
            if bid <= 0 or ask < bid or volume < 0 or oi < 0:
                raise ValueError(f"invalid point-in-time quote for token {token}")
            contracts.append(ChainContract(
                timestamp_ns=timestamp_ns,
                venue=_VENUE_BY_SEGMENT.get(exchange.strip().upper(), exchange.strip().upper()),
                underlying=underlying.strip().upper(),
                instrument_class=cls,
                expiry=int(expiry.strftime("%Y%m%d")),
                strike=strike,
                option_type=side,
                lot_size=lot_size,
                volume=volume,
                oi=oi,
                bid=bid,
                ask=ask,
            ))
        if not contracts:
            raise LookupError(f"no concrete {cls} option chain for {underlying} {expiry.isoformat()}")
        contracts.sort(key=lambda c: (c.strike, c.option_type))
        return ChainDiscoveryResult(
            underlying=underlying.strip().upper(),
            instrument_class=cls,
            exchange=exchange.strip().upper(),
            expiry=expiry,
            contracts=tuple(contracts),
        )


__all__ = ["ArbitrageInstrumentDiscovery", "ChainDiscoveryResult", "DiscoveredUniverse"]
