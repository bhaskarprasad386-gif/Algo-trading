"""Depth-aware quantity selection for Box Spread paper baskets.

Only broker-supplied depth levels are considered. Prices in Angel One depth
payloads are paise and are normalized to rupees; canonical L1 fields are already
rupees. This module is deliberately pure so the execution routes can validate
all four legs before creating a ledger fill.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def depth_levels(record: Any, side: str) -> list[tuple[float, float]]:
    """Return price-priority (price_rupees, quantity) levels for BID or ASK."""
    normalized_side = str(side or "").strip().upper()
    if normalized_side not in {"BID", "ASK"}:
        raise ValueError("side must be BID or ASK")
    if record is None:
        return []
    payload = getattr(record, "payload", {})
    payload = payload if isinstance(payload, Mapping) else {}
    raw_side = "buy" if normalized_side == "BID" else "sell"
    raw_levels = None
    for key in (f"best_5_{raw_side}_data", f"best_{raw_side}_data", f"best_{raw_side}", raw_side):
        candidate = payload.get(key)
        if isinstance(candidate, list) and candidate:
            raw_levels = candidate
            break
        if isinstance(candidate, Mapping):
            raw_levels = [candidate]
            break

    levels: list[tuple[float, float]] = []
    for level in raw_levels or []:
        if not isinstance(level, Mapping):
            continue
        price = _positive(level.get("price") or level.get(f"{raw_side}_price"))
        quantity = _positive(
            level.get("quantity") or level.get("qty") or level.get(f"{raw_side}_quantity")
        )
        if price is not None and quantity is not None:
            levels.append((price / 100.0, quantity))

    # Legacy/canonical records may have only L1 fields. Never extrapolate beyond L1.
    if not levels:
        price = _positive(getattr(record, "bid" if normalized_side == "BID" else "ask", None))
        quantity = _positive(
            getattr(record, "bid_qty" if normalized_side == "BID" else "ask_qty", None)
        )
        if price is not None and quantity is not None:
            levels = [(price, quantity)]

    levels.sort(key=lambda item: item[0], reverse=(normalized_side == "BID"))
    return levels


def allocate_depth(levels: Sequence[tuple[float, float]], quantity: int) -> dict[str, float | bool]:
    """Calculate executable VWAP for quantity, consuming best prices first."""
    if isinstance(quantity, bool) or int(quantity) != quantity or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    remaining = float(quantity)
    filled = 0.0
    notional = 0.0
    for price, available in levels:
        take = min(remaining, available)
        if take > 0:
            filled += take
            notional += take * price
            remaining -= take
        if remaining <= 1e-9:
            break
    return {
        "quantity": filled,
        "vwap": notional / filled if filled > 0 else 0.0,
        "complete": remaining <= 1e-9,
    }


def select_complete_lots(
    target_lots: int,
    lot_size: int,
    depth_by_role: Mapping[str, Sequence[tuple[float, float]]],
    *,
    roles: Sequence[str],
) -> dict[str, Any] | None:
    """Choose the largest complete four-leg quantity at or below target lots.

    Returns no candidate when even one lot cannot be filled on every leg.
    """
    if isinstance(target_lots, bool) or int(target_lots) != target_lots or target_lots < 1:
        raise ValueError("target_lots must be a positive integer")
    if isinstance(lot_size, bool) or int(lot_size) != lot_size or lot_size < 1:
        raise ValueError("lot_size must be a positive integer")
    if not roles or any(role not in depth_by_role for role in roles):
        return None

    for lots in range(int(target_lots), 0, -1):
        quantity = lots * int(lot_size)
        allocations = {
            role: allocate_depth(depth_by_role[role], quantity)
            for role in roles
        }
        if all(item["complete"] for item in allocations.values()):
            return {
                "lots": lots,
                "quantity": quantity,
                "allocations": allocations,
            }
    return None


def max_complete_lots(
    lot_size: int,
    depth_by_role: Mapping[str, Sequence[tuple[float, float]]],
    *,
    roles: Sequence[str],
) -> int:
    """Return the minimum whole-lot capacity across the four legs."""
    if isinstance(lot_size, bool) or int(lot_size) != lot_size or lot_size < 1:
        raise ValueError("lot_size must be a positive integer")
    if not roles or any(role not in depth_by_role for role in roles):
        return 0
    capacities = [
        int(sum(quantity for _, quantity in depth_by_role[role]) // int(lot_size))
        for role in roles
    ]
    return min(capacities) if capacities else 0


def limit_eligible_depth(
    levels: Sequence[tuple[float, float]],
    close_action: str,
    limit_price: float | None,
) -> list[tuple[float, float]]:
    """Keep only book levels that can execute at the requested closing limit.

    A sell-to-close limit can execute only at bids >= limit; a buy-to-close
    limit can execute only at asks <= limit. None means market/no price filter.
    """
    action = str(close_action or "").strip().upper()
    if action not in {"BUY", "SELL"}:
        raise ValueError("close_action must be BUY or SELL")
    if limit_price is None:
        return list(levels)
    limit = _positive(limit_price)
    if limit is None:
        raise ValueError("limit_price must be positive and finite")
    if action == "SELL":
        return [(price, qty) for price, qty in levels if price >= limit]
    return [(price, qty) for price, qty in levels if price <= limit]
