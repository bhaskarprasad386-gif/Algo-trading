"""Fail-closed Angel One index spot descriptor resolution for shared runner."""
from __future__ import annotations

from typing import Any

from .common_strategy_feed import CommonStrategyMarketFeed


_LOCKED_SPOT_UNDERLYINGS = ("NIFTY", "BANKNIFTY")


def resolve_locked_index_spot_descriptors(instrument_master: Any) -> tuple[Any, ...]:
    """Resolve only NIFTY and BANKNIFTY spot index tokens from Angel instrument master.

    The master resolver validates the concrete current index token. Descriptor
    identity is canonicalized to the locked underlying because Angel One rows
    may expose an empty or provider-specific symbol while resolving by name.
    """
    resolver = getattr(instrument_master, "resolve_index_instrument", None)
    if not callable(resolver):
        raise TypeError("instrument master must expose resolve_index_instrument")
    descriptors = []
    seen_tokens: set[str] = set()
    for underlying in _LOCKED_SPOT_UNDERLYINGS:
        row = resolver(underlying, "NSE")
        if not isinstance(row, dict):
            raise TypeError(f"index resolver returned an invalid row for {underlying}")
        token = str(row.get("token", "")).strip()
        if not token or token in seen_tokens:
            raise LookupError(f"invalid or duplicate index spot token for {underlying}")
        seen_tokens.add(token)
        # The lifecycle requires symbol == underlying; never copy a blank or
        # provider-specific symbol into this locked index descriptor.
        descriptors.append(CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token=token, symbol=underlying, instrument_type="index",
            segment="NSE", underlying=underlying,
        ))
    if len(descriptors) != len(_LOCKED_SPOT_UNDERLYINGS):
        raise LookupError("locked spot universe resolution incomplete")
    return tuple(descriptors)


__all__ = ["resolve_locked_index_spot_descriptors"]
