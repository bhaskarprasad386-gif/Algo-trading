"""Shadow-only Butterfly and Condor calculations over Box Spread's shared snapshot.

This module consumes existing quote/depth records. It never subscribes, fetches
instruments, polls a broker, publishes Box Spread results, or routes orders.
"""
from __future__ import annotations

from itertools import combinations
from math import isfinite
from typing import Any, Iterable, Mapping


_STRATEGIES = {
    "LONG_CREDIT_BUTTERFLY": ("BUTTERFLY", "LONG"),
    "SHORT_CREDIT_BUTTERFLY": ("BUTTERFLY", "SHORT"),
    "LONG_CREDIT_CONDOR": ("CONDOR", "LONG"),
    "SHORT_CREDIT_CONDOR": ("CONDOR", "SHORT"),
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _whole(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _level_values(level: Any) -> tuple[float | None, float | None]:
    if isinstance(level, Mapping):
        return _number(level.get("price")), _number(level.get("quantity"))
    if isinstance(level, (tuple, list)) and len(level) >= 2:
        return _number(level[0]), _number(level[1])
    return None, None


def _levels_quantity(levels: Iterable[Any]) -> float:
    total = 0.0
    for level in levels:
        price, quantity = _level_values(level)
        if price is not None and price > 0 and quantity is not None and quantity > 0:
            total += quantity
    return total


def _vwap(levels: Iterable[Any], quantity: int) -> float | None:
    remaining = float(quantity)
    notional = 0.0
    filled = 0.0
    for level in levels:
        price, available = _level_values(level)
        if price is None or price <= 0 or available is None or available <= 0:
            continue
        take = min(remaining, available)
        notional += take * price
        filled += take
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or filled <= 0:
        return None
    return notional / filled


def _bounded_strikes(strikes: list[float], atm: float, instrument_class: str) -> list[float]:
    """Keep only actual listed strikes within Box's existing ATM radius."""
    if atm <= 0 or not isfinite(atm):
        return []
    radius = 15 if instrument_class == "INDEX" else 7
    below = [strike for strike in strikes if strike < atm][-radius:]
    above = [strike for strike in strikes if strike > atm][:radius]
    exact = [strike for strike in strikes if strike == atm]
    return sorted(below + exact + above)


def _structure_candidates(quotes: Mapping[float, dict], atm: float, instrument_class: str):
    strikes = _bounded_strikes(sorted(quotes), atm, instrument_class)
    for count, kind in ((3, "BUTTERFLY"), (4, "CONDOR")):
        for legs in combinations(strikes, count):
            gaps = [legs[i + 1] - legs[i] for i in range(count - 1)]
            if not gaps or any(gap <= 0 for gap in gaps):
                continue
            # Symmetric butterfly wings; equally spaced condor body/wings.
            if max(gaps) - min(gaps) > 1e-8:
                continue
            yield kind, legs, gaps[0]


def _leg_plan(kind: str, direction: str, strikes: tuple[float, ...], option_type: str):
    if direction == "LONG":
        return [(strike, option_type, "BUY" if i in (0, len(strikes) - 1) else "SELL",
                 2 if kind == "BUTTERFLY" and i == 1 else 1)
                for i, strike in enumerate(strikes)]
    return [(strike, option_type, "SELL" if i in (0, len(strikes) - 1) else "BUY",
             2 if kind == "BUTTERFLY" and i == 1 else 1)
            for i, strike in enumerate(strikes)]


def _credit_from_prices(plan, quotes, *, depth=None):
    credit = 0.0
    for strike, _option_type, action, multiplier in plan:
        quote = quotes[strike]
        if depth is None:
            price = quote["ask"] if action == "BUY" else quote["bid"]
        else:
            price = depth[strike]
        credit += (-1.0 if action == "BUY" else 1.0) * price * multiplier
    return credit


def _l1_capacity(plan, quotes, lot_size: int) -> int:
    capacities = []
    for strike, option_type, action, multiplier in plan:
        quote = quotes[strike]
        quantity = _whole(quote.get("ask_qty" if action == "BUY" else "bid_qty"))
        if quantity is None:
            return 0
        capacities.append(quantity // (lot_size * multiplier))
    return min(capacities, default=0)


def _depth_result(plan, quotes, depth_by_identity, *, underlying, expiry, lot_size,
                  max_leg_skew_seconds):
    selected = {}
    timestamps = []
    capacities = []
    for strike, option_type, action, multiplier in plan:
        item = depth_by_identity.get((underlying, expiry, strike, option_type))
        if not item or str(item.get("freshness") or "").upper() != "FRESH":
            return {"status": "INCOMPLETE", "reason": "MISSING_OR_STALE_DEPTH"}
        quote_ts = int(quotes[strike]["timestamp_ns"])
        depth_ts = int(item.get("timestamp_ns") or 0)
        if depth_ts <= 0 or abs(depth_ts - quote_ts) > max_leg_skew_seconds * 1e9:
            return {"status": "INCOMPLETE", "reason": "QUOTE_DEPTH_TIMESTAMP_SKEW"}
        levels = item.get("asks" if action == "BUY" else "bids") or []
        if not levels:
            return {"status": "INCOMPLETE", "reason": "MISSING_EXECUTION_SIDE_DEPTH"}
        total = _levels_quantity(levels)
        capacities.append(int(total // (lot_size * multiplier)))
        selected[(strike, option_type, action)] = levels
        timestamps.append(depth_ts)
    lots = min(capacities, default=0)
    if lots <= 0:
        return {"status": "INCOMPLETE", "reason": "INSUFFICIENT_COMMON_LOT_DEPTH"}
    prices = {}
    for strike, option_type, action, multiplier in plan:
        levels = selected[(strike, option_type, action)]
        price = _vwap(levels, lots * lot_size * multiplier)
        if price is None:
            return {"status": "INCOMPLETE", "reason": "DEPTH_VWAP_UNAVAILABLE"}
        prices[strike] = price
    return {
        "status": "READY",
        "executable_lots": lots,
        "credit_per_unit": _credit_from_prices(plan, quotes, depth=prices),
        "leg_vwap": [
            {"strike": strike, "option_type": option_type, "action": action,
             "multiplier": multiplier, "vwap": prices[strike]}
            for strike, option_type, action, multiplier in plan
        ],
        "max_timestamp_skew_seconds": round((max(timestamps) - min(timestamps)) / 1e9, 6),
    }


def _known_expiry_type(underlying: str, expiry: int) -> str:
    # Reuse classification already derived from the active Box Spread
    # instrument master; never infer expiry type from weekday arithmetic.
    from app.scanner.live_box_spread_routes import classify_expiry_type
    return str(classify_expiry_type(underlying, expiry) or "UNKNOWN").strip().upper()


def _eligible_index_expiry(underlying: str, expiry_type: str) -> bool:
    symbol = str(underlying or "").strip().upper()
    kind = str(expiry_type or "").strip().upper()
    if symbol in {"NIFTY", "SENSEX"}:
        return kind in {"WEEKLY", "MONTHLY"}
    if symbol == "BANKNIFTY":
        return kind == "MONTHLY"
    return False


def evaluate_butterfly_condor_snapshot(
    snapshot: Mapping[str, Any],
    *,
    fees_per_unit: float = 0.0,
    max_age_seconds: float = 5.0,
    max_leg_skew_seconds: float = 2.0,
    expiry_type_resolver=None,
    min_gross_expiry_profit_per_unit: float = 5.0,
) -> dict[str, Any]:
    """Evaluate four credit structures independently for CE and PE.

    Results are indicative, shadow-only L1/depth calculations; no alert or
    order side effect is performed here. Positive gross credit is required.
    """
    fees = _number(fees_per_unit)
    max_age = _number(max_age_seconds)
    max_skew = _number(max_leg_skew_seconds)
    if fees is None or fees < 0:
        raise ValueError("fees_per_unit must be finite and non-negative")
    if max_age is None or not 0 < max_age <= 60:
        raise ValueError("max_age_seconds must be in (0, 60]")
    if max_skew is None or not 0 < max_skew <= 30:
        raise ValueError("max_leg_skew_seconds must be in (0, 30]")
    minimum_profit = _number(min_gross_expiry_profit_per_unit)
    if minimum_profit is None or minimum_profit < 0:
        raise ValueError("min_gross_expiry_profit_per_unit must be finite and non-negative")
    resolver = expiry_type_resolver or _known_expiry_type
    quotes_snapshot = snapshot.get("quotes") if isinstance(snapshot, Mapping) else None
    if not isinstance(quotes_snapshot, Mapping):
        raise ValueError("shared market snapshot must contain quotes")
    captured_ns = int(quotes_snapshot.get("captured_at_ns") or 0)
    groups = quotes_snapshot.get("groups") or []
    depth_snapshot = snapshot.get("depth") if isinstance(snapshot, Mapping) else {}
    depth_rows = (depth_snapshot or {}).get("records") or []
    depth_by_identity = {}
    for row in depth_rows:
        if not isinstance(row, Mapping):
            continue
        key = (str(row.get("underlying") or "").upper(), int(row.get("expiry") or 0),
               _number(row.get("strike")), str(row.get("option_type") or "").upper())
        if key[2] is not None:
            depth_by_identity[key] = row

    results = []
    rejected = {"STALE_OR_INVALID_QUOTE": 0, "INVALID_GROUP": 0}
    for group in groups:
        if not isinstance(group, Mapping):
            rejected["INVALID_GROUP"] += 1
            continue
        underlying = str(group.get("underlying") or "").strip().upper()
        expiry = _whole(group.get("expiry"))
        atm = _number(group.get("atm_strike"))
        rows = group.get("data") or []
        if not underlying or expiry is None or atm is None:
            rejected["INVALID_GROUP"] += 1
            continue
        try:
            expiry_type = str(resolver(underlying, expiry) or "UNKNOWN").strip().upper()
        except Exception:
            expiry_type = "UNKNOWN"
        if not _eligible_index_expiry(underlying, expiry_type):
            rejected["INELIGIBLE_INDEX_OR_EXPIRY"] = rejected.get("INELIGIBLE_INDEX_OR_EXPIRY", 0) + 1
            continue
        for option_type in ("CE", "PE"):
            by_strike = {}
            for row in rows:
                if not isinstance(row, Mapping) or str(row.get("option_type") or "").upper() != option_type:
                    continue
                strike = _number(row.get("strike"))
                bid, ask = _number(row.get("bid")), _number(row.get("ask"))
                lot = _whole(row.get("lot_size"))
                stamp = _whole(row.get("timestamp_ns"))
                age = (captured_ns - stamp) / 1e9 if stamp is not None and captured_ns else float("inf")
                if (strike is None or strike <= 0 or bid is None or ask is None or bid <= 0
                        or ask < bid or lot is None or lot <= 0 or stamp is None
                        or age < -1.0 or age > max_age or str(row.get("freshness") or "").upper() != "LIVE"):
                    rejected["STALE_OR_INVALID_QUOTE"] += 1
                    continue
                by_strike[strike] = dict(row, strike=strike, bid=bid, ask=ask,
                                          lot_size=lot, timestamp_ns=stamp)
            for kind, strikes, step in _structure_candidates(by_strike, atm, str(
                (rows[0].get("instrument_class") if rows and isinstance(rows[0], Mapping) else "INDEX") or "INDEX"
            ).upper()):
                leg_quotes = {strike: by_strike[strike] for strike in strikes}
                lot_sizes = {row["lot_size"] for row in leg_quotes.values()}
                stamps = [row["timestamp_ns"] for row in leg_quotes.values()]
                if len(lot_sizes) != 1 or (max(stamps) - min(stamps)) > max_skew * 1e9:
                    continue
                lot_size = next(iter(lot_sizes))
                for strategy_name, (strategy_kind, direction) in _STRATEGIES.items():
                    if strategy_kind != kind:
                        continue
                    plan = _leg_plan(kind, direction, strikes, option_type)
                    credit = _credit_from_prices(plan, leg_quotes)
                    if credit <= 0:
                        continue
                    minimum_expiry_pnl = credit if direction == "LONG" else credit - step
                    # Strict threshold: exactly 5.00 points is not eligible.
                    if minimum_expiry_pnl <= minimum_profit:
                        continue
                    l1_lots = _l1_capacity(plan, leg_quotes, lot_size)
                    depth = _depth_result(
                        plan, leg_quotes, depth_by_identity, underlying=underlying,
                        expiry=expiry, lot_size=lot_size, max_leg_skew_seconds=max_skew,
                    )
                    results.append({
                        "strategy": strategy_name,
                        "strategy_type": kind,
                        "direction": direction,
                        "option_type": option_type,
                        "underlying": underlying,
                        "expiry": expiry,
                        "expiry_type": expiry_type,
                        "strikes": list(strikes),
                        "step": step,
                        "lot_size": lot_size,
                        "timestamp_ns": max(stamps),
                        "gross_credit_per_unit": credit,
                        "gross_credit_per_lot": credit * lot_size,
                        "estimated_fees_per_unit": fees,
                        "estimated_net_credit_per_unit": credit - fees,
                        "minimum_gross_expiry_pnl_per_unit": minimum_expiry_pnl,
                        "maximum_gross_expiry_loss_per_unit": max(0.0, -minimum_expiry_pnl),
                        "l1_executable_lots": l1_lots,
                        "depth": depth,
                        "legs": [
                            {"strike": strike, "option_type": option_type,
                             "action": action, "multiplier": multiplier,
                             "entry_price": (leg_quotes[strike]["ask"] if action == "BUY"
                                             else leg_quotes[strike]["bid"])}
                            for strike, option_type, action, multiplier in plan
                        ],
                        "execution_mode": "SHADOW_ONLY",
                        "order_routing_enabled": False,
                    })
    results.sort(key=lambda row: (row["underlying"], row["expiry"], row["option_type"],
                                  row["strategy"], -row["gross_credit_per_unit"]))
    return {
        "schema_version": 1,
        "source": "box-spread-shared-snapshot",
        "read_only": True,
        "execution_mode": "SHADOW_ONLY",
        "order_routing_enabled": False,
        "result_count": len(results),
        "rejected_counts": rejected,
        "eligibility": {
            "allowed_expiries": {
                "NIFTY": ["WEEKLY", "MONTHLY"],
                "SENSEX": ["WEEKLY", "MONTHLY"],
                "BANKNIFTY": ["MONTHLY"],
            },
            "minimum_gross_expiry_profit_per_unit_exclusive": minimum_profit,
        },
        "results": results,
    }


__all__ = ["evaluate_butterfly_condor_snapshot"]
