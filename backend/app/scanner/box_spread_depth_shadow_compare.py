"""Pure, opt-in comparison of Box Spread L1 edge with four-leg depth VWAP.

This module has no feed, database, scanner, alert, or order side effects. Callers
must map the exact candidate contracts to the four role records.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence

_ROLES = ("low_call", "high_call", "high_put", "low_put")


def _valid_levels(value: Any, *, side: str) -> list[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        return []
    parsed = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            price, quantity = float(item[0]), float(item[1])
        except (TypeError, ValueError, OverflowError):
            continue
        if not isfinite(price) or not isfinite(quantity) or price <= 0 or quantity <= 0:
            continue
        parsed.append((price, quantity))
    parsed.sort(key=lambda level: level[0], reverse=(side == "BID"))
    return parsed[:5]


def _vwap(levels: Sequence[tuple[float, float]], quantity: int) -> float | None:
    remaining = float(quantity)
    notional = 0.0
    filled = 0.0
    for price, available in levels:
        take = min(remaining, available)
        if take > 0:
            filled += take
            notional += take * price
            remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or filled <= 0:
        return None
    return notional / filled


def compare_box_depth(
    candidate: Any,
    depth_by_role: Mapping[str, Mapping[str, Any]],
    *,
    target_lots: int = 1,
    now_ns: int | None = None,
    now_monotonic: float | None = None,
    max_provider_age_seconds: float = 5.0,
    max_arrival_age_seconds: float = 5.0,
    max_cross_leg_skew_seconds: float = 2.0,
) -> dict[str, Any]:
    """Compare same-quantity L1 and depth edge; incomplete baskets fail closed.

    depth_by_role keys are low_call/high_call/high_put/low_put. Each value is a
    shadow-feed snapshot record with freshness, depth_status, timestamp_ns,
    provider_age_seconds, arrival_age_seconds, bids and asks.
    """
    from time import monotonic, time_ns

    if isinstance(target_lots, bool) or int(target_lots) != target_lots or target_lots < 1:
        raise ValueError("target_lots must be a positive integer")
    low, high = candidate.low, candidate.high
    lot_size = int(low.lot_size)
    if lot_size <= 0 or lot_size != int(high.lot_size):
        return {"status": "INCOMPLETE", "reason": "LOT_SIZE_MISMATCH"}
    quantity = lot_size * int(target_lots)
    direction = str(candidate.direction).upper()
    if direction not in {"LONG", "SHORT"}:
        return {"status": "INCOMPLETE", "reason": "INVALID_DIRECTION"}
    if any(role not in depth_by_role for role in _ROLES):
        return {"status": "INCOMPLETE", "reason": "MISSING_LEG", "quantity": quantity}

    wall_now = time_ns() if now_ns is None else int(now_ns)
    arrival_now = monotonic() if now_monotonic is None else float(now_monotonic)
    records = [depth_by_role[role] for role in _ROLES]
    stamps = []
    for record in records:
        if not isinstance(record, Mapping):
            return {"status": "INCOMPLETE", "reason": "INVALID_LEG_RECORD", "quantity": quantity}
        if record.get("freshness") != "FRESH" or record.get("depth_status") not in {"DEPTH_FULL", "DEPTH_PARTIAL"}:
            return {"status": "INCOMPLETE", "reason": "DEPTH_NOT_EXECUTABLE", "quantity": quantity}
        try:
            provider_age = float(record["provider_age_seconds"])
            arrival_age = float(record["arrival_age_seconds"])
            stamp = int(record["timestamp_ns"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"status": "INCOMPLETE", "reason": "INVALID_TIMESTAMP", "quantity": quantity}
        if not isfinite(provider_age) or not isfinite(arrival_age):
            return {"status": "INCOMPLETE", "reason": "INVALID_TIMESTAMP", "quantity": quantity}
        if not (-1.0 <= provider_age <= max_provider_age_seconds and 0 <= arrival_age <= max_arrival_age_seconds):
            return {"status": "INCOMPLETE", "reason": "STALE_LEG", "quantity": quantity}
        stamps.append(stamp)
    if (max(stamps) - min(stamps)) / 1e9 > max_cross_leg_skew_seconds:
        return {"status": "INCOMPLETE", "reason": "CROSS_LEG_SKEW", "quantity": quantity}

    # LONG buys low CE/high PE and sells high CE/low PE. SHORT reverses.
    actions = (
        {"low_call": "ASK", "high_call": "BID", "high_put": "ASK", "low_put": "BID"}
        if direction == "LONG"
        else {"low_call": "BID", "high_call": "ASK", "high_put": "BID", "low_put": "ASK"}
    )
    allocations = {}
    for role in _ROLES:
        side = actions[role]
        levels = _valid_levels(records[_ROLES.index(role)].get("asks" if side == "ASK" else "bids"), side=side)
        vwap = _vwap(levels, quantity)
        if vwap is None:
            return {"status": "INCOMPLETE", "reason": "INSUFFICIENT_DEPTH", "quantity": quantity, "actions": actions}
        allocations[role] = {"side": side, "vwap": vwap}

    width = float(high.strike) - float(low.strike)
    if not isfinite(width) or width <= 0:
        return {"status": "INCOMPLETE", "reason": "INVALID_STRIKE_WIDTH", "quantity": quantity}
    if direction == "LONG":
        depth_edge_per_unit = width - (
            allocations["low_call"]["vwap"] - allocations["high_call"]["vwap"]
            + allocations["high_put"]["vwap"] - allocations["low_put"]["vwap"]
        )
    else:
        depth_edge_per_unit = (
            allocations["low_call"]["vwap"] - allocations["high_call"]["vwap"]
            + allocations["high_put"]["vwap"] - allocations["low_put"]["vwap"]
        ) - width
    l1_edge = float(candidate.executable_edge)
    if not isfinite(l1_edge):
        return {"status": "INCOMPLETE", "reason": "INVALID_L1_EDGE", "quantity": quantity}
    return {
        "status": "COMPLETE",
        "direction": direction,
        "quantity": quantity,
        "lots": int(target_lots),
        "l1_edge_per_unit": l1_edge,
        "depth_edge_per_unit": depth_edge_per_unit,
        "edge_difference_per_unit": depth_edge_per_unit - l1_edge,
        "l1_edge_total": l1_edge * quantity,
        "depth_edge_total": depth_edge_per_unit * quantity,
        "allocations": allocations,
        "provider_timestamp_skew_seconds": round((max(stamps) - min(stamps)) / 1e9, 6),
    }


__all__ = ["compare_box_depth"]
