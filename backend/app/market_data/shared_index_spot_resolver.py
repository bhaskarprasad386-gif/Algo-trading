"""Fail-closed Angel One index spot descriptor resolution for shared runner."""
from __future__ import annotations

from typing import Any

from .common_strategy_feed import CommonStrategyMarketFeed


_LOCKED_SPOT_UNDERLYINGS = ("NIFTY", "BANKNIFTY")


def resolve_locked_index_spot_descriptors(instrument_master: Any) -> tuple[Any, ...]:
    """Resolve only NIFTY and BANKNIFTY spot index tokens from Angel instrument master.



    Requires the master resolver to return a single validated current index token;
    never falls back to cash-equity tokens or fuzzy matches.
    """
    resolver = getattr(instrument_master, "resolve_index_instrument", None)
    if not callable(resolver):
        raise TypeError("instrument master must expose resolve_index_instrument")
    descriptors = []
    seen_tokens: set[str] = set()
    for underlying in _LOCKED_SPOT_UNDERLYINGS:
        row = resolver(underlying, "NSE")
        token = str(row.get("token", "")).strip()
        symbol = str(row.get("symbol", row.get("name", underlying))).strip()
        if not token or token in seen_tokens:
            raise LookupError(f"invalid or duplicate index spot token for {underlying}")
        seen_tokens.add(token)
        descriptors.append(CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token=token, symbol=symbol, instrument_type="index",
            segment="NSE", underlying=underlying,
        ))
    if len(descriptors) != len(_LOCKED_SPOT_UNDERLYINGS):
        raise LookupError("locked spot universe resolution incomplete")
    return tuple(descriptors)


__all__ = ["resolve_locked_index_spot_descriptors"]
