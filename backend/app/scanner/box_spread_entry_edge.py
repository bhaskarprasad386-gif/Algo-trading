"""Alert-edge protection for four-leg Box Spread paper entries.

The alert edge is an immutable reference captured when the paper trade is
created. Entry eligibility is evaluated from the actual proposed four-leg
prices, not from a later scanner edge.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

ROLES = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")


def minimum_entry_edge(alert_edge: object) -> float:
    """Return the minimum permitted entry edge for the alert-edge band."""
    try:
        edge = float(alert_edge)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("alert edge must be a finite number") from None
    if not math.isfinite(edge):
        raise ValueError("alert edge must be a finite number")
    if 5.0 <= edge < 11.0:
        return edge - 1.0
    if 11.0 <= edge < 21.0:
        return edge - 2.0
    if edge >= 21.0:
        return edge - 3.0
    raise ValueError("alert edge below 5 is not eligible for paper entry")


def effective_entry_edge(
    *,
    direction: str,
    low_strike: object,
    high_strike: object,
    reference_prices: Mapping[str, object],
    limit_prices: Mapping[str, object] | None = None,
) -> float:
    """Calculate edge using conservative per-leg prices for the proposed limits.

    BUY legs are charged at the higher of depth VWAP and limit; SELL legs are
    credited at the lower. This prevents a permissive limit from overstating
    the edge available at entry.
    """
    side = str(direction or "").strip().upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if set(reference_prices) != set(ROLES):
        raise ValueError("reference prices must contain exactly four Box Spread legs")
    if limit_prices is not None and set(limit_prices) != set(ROLES):
        raise ValueError("limit prices must contain exactly four Box Spread legs")
    try:
        low = float(low_strike)
        high = float(high_strike)
        prices = {role: float(reference_prices[role]) for role in ROLES}
        limits = ({role: float(limit_prices[role]) for role in ROLES}
                  if limit_prices is not None else {})
    except (TypeError, ValueError, OverflowError, KeyError):
        raise ValueError("strikes and all leg prices must be finite positive numbers") from None
    values = [low, high, *prices.values(), *limits.values()]
    if any(not math.isfinite(value) or value <= 0 for value in values) or low >= high:
        raise ValueError("strikes and all leg prices must be finite positive numbers")

    buy_roles = ({"LOW_CE", "HIGH_PE"} if side == "LONG"
                 else {"HIGH_CE", "LOW_PE"})
    conservative = {}
    for role in ROLES:
        reference = prices[role]
        if role not in limits:
            conservative[role] = reference
        elif role in buy_roles:
            conservative[role] = max(reference, limits[role])
        else:
            conservative[role] = min(reference, limits[role])

    signed_net = (
        conservative["LOW_CE"] + conservative["HIGH_PE"]
        - conservative["HIGH_CE"] - conservative["LOW_PE"]
    )
    width = high - low
    return width - signed_net if side == "LONG" else signed_net - width


def validate_entry_edge(
    *,
    alert_edge: object,
    direction: str,
    low_strike: object,
    high_strike: object,
    reference_prices: Mapping[str, object],
    limit_prices: Mapping[str, object] | None = None,
) -> dict[str, float]:
    """Raise when proposed prices breach the immutable alert-edge floor."""
    minimum = minimum_entry_edge(alert_edge)
    actual = effective_entry_edge(
        direction=direction, low_strike=low_strike, high_strike=high_strike,
        reference_prices=reference_prices, limit_prices=limit_prices,
    )
    if actual + 1e-9 < minimum:
        raise ValueError(
            f"entry edge {actual:.4f} is below alert-based minimum {minimum:.4f}; "
            "all four paper legs rejected"
        )
    return {"alert_edge": float(alert_edge), "minimum_entry_edge": minimum,
            "effective_entry_edge": actual}
