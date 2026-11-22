"""VPT-X reversal intelligence for NIFTY and BANKNIFTY.

Pure evaluator: no broker subscriptions, network, persistence, or order routing.
Consumes normalized timestamped market observations and emits scanner-only candidates.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence

SUPPORTED_UNDERLYINGS = {"NIFTY", "BANKNIFTY"}
PATTERNS = ("SWING_EXHAUSTION", "COMPRESSION_REVERSAL", "V_REVERSAL")
MAX_TICKS = 500
MAX_AGE_MS = 5_000
MIN_TICKS = 24


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeTypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _ratio(n: float, d: float) -> float:
    return n / d if abs(d) > 1e-12 else 0.0


def _normalize_ticks(raw: Sequence[Any]) -> list[dict[str, float]]:
    ticks: list[dict[str, float]] = []
    for item in raw[-MAX_TICKS:]:
        if not isinstance(item, Mapping):
            continue
        ts = _finite(item.get("timestamp_ms"))
        price = _finite(item.get("price"))
        if ts is None or price is None or ts <= 0 or price <= 0:
            continue
        tick = {"timestamp_ms": ts, "price": price}
        for field in ("buy_volume", "sell_volume", "bid_qty", "ask_qty", "bid", "ask"):
            tick[field] = max(0.0, _finite(item.get(field)) or 0.0)
        ticks.append(tick)
    ticks.sort(key=lambda t: t["timestamp_ms"])
    dedup: dict[float, dict[str, float]] = {}
    for tick in ticks:
        dedup[tick["timestamp_ms"]] = tick
    return list(dedup.values())


def _flow(ticks: Sequence[Mapping[str, float]]) -> tuple[float, float]:
    buys = sum(t.get("buy_volume", 0.0) for t in ticks)
    sells = sum(t.get("sell_volume", 0.0) for t in ticks)
    total = buys + sells
    return (_ratio(buys - sells, total), total)


def _impact(ticks: Sequence[Mapping[str, float]]) -> float:
    if len(ticks) < 2:
        return 0.0
    delta = abs(ticks[-1]["price"] - ticks[0]["price"])
    _, volume = _flow(ticks)
    return _ratio(delta, volume)


def _book_imbalance(tick: Mapping[str, float]) -> float:
    bid, ask = tick.get("bid_qty", 0.0), tick.get("ask_qty", 0.0)
    return _ratio(bid - ask, bid + ask)


def _pivot_break(ticks: Sequence[Mapping[str, float]], direction: str) -> bool:
    if len(ticks) < 8:
        return False
    recent = ticks[-4:]
    prior = ticks[-8:-4]
    if direction == "BUY":
        return max(t["price"] for t in recent) > max(t["price"] for t in prior)
    return min(t["price"] for t in recent) < min(t["price"] for t in prior)


def _metrics(ticks: Sequence[Mapping[str, float]]) -> dict[str, float | bool]:
    prices = [t["price"] for t in ticks]
    low, high = min(prices), max(prices)
    latest = ticks[-1]
    flow_delta, flow_volume = _flow(ticks)
    n = max(8, len(ticks) // 3)
    old, new = ticks[:-n], ticks[-n:]
    impact_old, impact_new = _impact(old), _impact(new)
    impact_decay = _clamp(1.0 - _ratio(impact_new, impact_old)) if impact_old > 0 else 0.0
    old_delta, _ = _flow(old)
    new_delta, _ = _flow(new)
    buy_flip = new_delta > max(0.08, old_delta + 0.05)
    sell_flip = new_delta < min(-0.08, old_delta - 0.05)
    ref = ticks[:-max(4, len(ticks) // 5)] or ticks
    ref_low, ref_high = min(t["price"] for t in ref), max(t["price"] for t in ref)
    price_range = max(high - low, latest["price"] * 0.00005)
    sweep_down = latest["price"] < ref_low and (ref_low - latest["price"]) <= price_range * 0.35
    reclaim_low = latest["price"] >= ref_low
    sweep_up = latest["price"] > ref_high and (latest["price"] - ref_high) <= price_range * 0.35
    reject_high = latest["price"] <= ref_high
    mid = max(4, len(ticks) // 2)
    first, second = ticks[:mid], ticks[mid:]
    second_low_fail = min(t["price"] for t in second) >= min(t["price"] for t in first) - price_range * 0.025
    second_high_fail = max(t["price"] for t in second) <= max(t["price"] for t in first) + price_range * 0.025
    disp = _ratio(latest["price"] - ticks[-5]["price"], price_range) if len(ticks) >= 5 else 0.0
    book = _book_imbalance(latest)
    return {
        "flow_delta": flow_delta, "flow_volume": flow_volume,
        "impact_decay": impact_decay, "buy_flip": buy_flip, "sell_flip": sell_flip,
        "sweep_down": sweep_down, "reclaim_low": reclaim_low,
        "sweep_up": sweep_up, "reject_high": reject_high,
        "second_low_fail": second_low_fail, "second_high_fail": second_high_fail,
        "normalized_displacement": disp, "book_imbalance": book,
        "range": price_range, "ref_low": ref_low, "ref_high": ref_high,
        "low": low, "high": high, "price": latest["price"],
    }


def _pivot_break_for_metrics(direction: str, m: Mapping[str, Any]) -> bool:
    return abs(m["normalized_displacement"]) >= 0.04 and (
        m["normalized_displacement"] > 0 if direction == "BUY" else m["normalized_displacement"] < 0
    )


def _score(direction: str, m: Mapping[str, Any], pattern: str) -> tuple[int, dict[str, int], list[str]]:
    flow = m["buy_flip"] if direction == "BUY" else m["sell_flip"]
    absorption = m["second_low_fail"] if direction == "BUY" else m["second_high_fail"]
    sweep = (m["sweep_down"] or m["reclaim_low"]) if direction == "BUY" else (m["sweep_up"] or m["reject_high"])
    pivot = _pivot_break_for_metrics(direction, m)
    book = m["book_imbalance"] > 0.12 if direction == "BUY" else m["book_imbalance"] < -0.12
    displacement = m["normalized_displacement"] > 0.025 if direction == "BUY" else m["normalized_displacement"] < -0.025
    impact = m["impact_decay"] >= 0.25
    components = {
        "liquidity_failure": 25 if sweep else 0,
        "directional_force_decay": 20 if impact else 0,
        "opposite_flow_transition": 20 if flow else 0,
        "repeated_attack_failure": 15 if absorption else 0,
        "microstructure_pressure": 10 if book else 0,
        "early_displacement": 10 if pivot or displacement else 0,
    }
    score = sum(components.values())
    if pattern == "SWING_EXHAUSTION":
        pattern_gate = sweep and impact and (flow or absorption)
    elif pattern == "COMPRESSION_REVERSAL":
        pattern_gate = flow and pivot and absorption
    else:
        pattern_gate = impact and flow and (pivot or displacement)
    evidence = [name for name, points in components.items() if points > 0]
    return score, components, evidence + ([] if pattern_gate else ["PATTERN_GATE_MISSING"])


def evaluate_vptx(snapshot: Mapping[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
    """Return six candidates (3 patterns x 2 directions), never trading signals."""
    underlying = str(snapshot.get("underlying", "")).upper().strip()
    if underlying not in SUPPORTED_UNDERLYINGS:
        return {"underlying": underlying or None, "status": "REJECTED", "reason": "UNSUPPORTED_UNDERLYING",
                "candidates": [], "scanner_only": True, "order_routing_enabled": False}
    ticks = _normalize_ticks(snapshot.get("ticks", []))
    now = now_ms if now_ms is not None else (ticks[-1]["timestamp_ms"] if ticks else 0)
    age = now - ticks[-1]["timestamp_ms"] if ticks else None
    valid = len(ticks) >= MIN_TICKS and age is not None and 0 <= age <= MAX_AGE_MS
    if not valid:
        return {"underlying": underlying, "status": "WAITING_DATA", "tick_count": len(ticks),
                "quote_age_ms": age, "reason": "INSUFFICIENT_OR_STALE_TICKS",
                "candidates": [], "scanner_only": True, "order_routing_enabled": False}
    m = _metrics(ticks)
    cross = _clamp(_finite(snapshot.get("cross_confirmation")) or 0.0)
    candidates = []
    for pattern in PATTERNS:
        for direction in ("BUY", "SELL"):
            score, components, evidence = _score(direction, m, pattern)
            if cross >= 0.7:
                score = min(100, score + 5)
                components["cross_market_confirmation"] = 5
            pattern_gate = "PATTERN_GATE_MISSING" not in evidence
            trigger = pattern_gate and score >= 65 and _pivot_break(ticks, direction)
            candidates.append({
                "pattern": pattern, "direction": direction, "score": score,
                "strength": "STRONG" if score >= 75 else "DEVELOPING" if score >= 55 else "WATCH",
                "components": components, "evidence": evidence, "trigger_ready": bool(trigger),
                "entry_style": "EARLY_CONTROL_SHIFT" if trigger else "WATCH",
                "invalidation_price": m["low"] if direction == "BUY" else m["high"],
                "target_reference": "VWAP_OR_NEXT_LIQUIDITY_ZONE",
                "scanner_only": True, "order_routing_enabled": False,
            })
    candidates.sort(key=lambda c: (c["trigger_ready"], c["score"]), reverse=True)
    return {"underlying": underlying, "status": "EVALUATED", "tick_count": len(ticks),
            "quote_age_ms": age, "features": m, "candidates": candidates,
            "scanner_only": True, "order_routing_enabled": False,
            "subscription_enabled": False, "broker_integration": "NOT_CONFIGURED"}
