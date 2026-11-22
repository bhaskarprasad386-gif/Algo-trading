"""Isolated VPT-X reversal evaluator for NIFTY and BANKNIFTY.

Pure, deterministic scanner logic. No broker/network/feed subscriptions, persistence,
paper execution, or order routing.
"""
from __future__ import annotations

from math import isfinite
import time
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
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _ratio(n: float, d: float) -> float:
    return n / d if abs(d) > 1e-12 else 0.0


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _normalize_ticks(raw: Sequence[Any]) -> list[dict[str, float]]:
    ticks: list[dict[str, float]] = []
    for item in raw[-MAX_TICKS:]:
        if not isinstance(item, Mapping):
            continue
        timestamp, price = _finite(item.get("timestamp_ms")), _finite(item.get("price"))
        if timestamp is None or price is None or timestamp <= 0 or price <= 0:
            continue
        tick = {"timestamp_ms": timestamp, "price": price}
        for field in ("buy_volume", "sell_volume", "bid_qty", "ask_qty", "bid", "ask"):
            tick[field] = max(0.0, _finite(item.get(field)) or 0.0)
        ticks.append(tick)
    ticks.sort(key=lambda row: row["timestamp_ms"])
    deduplicated: dict[float, dict[str, float]] = {}
    for tick in ticks:
        deduplicated[tick["timestamp_ms"]] = tick
    return list(deduplicated.values())


def _flow(ticks: Sequence[Mapping[str, float]]) -> tuple[float, float, float, float]:
    buys = sum(t.get("buy_volume", 0.0) for t in ticks)
    sells = sum(t.get("sell_volume", 0.0) for t in ticks)
    total = buys + sells
    return _ratio(buys - sells, total), total, buys, sells


def _impact(ticks: Sequence[Mapping[str, float]], side: str) -> float:
    if len(ticks) < 2:
        return 0.0
    _, _, buys, sells = _flow(ticks)
    volume = buys if side == "BUY" else sells
    signed_move = ticks[-1]["price"] - ticks[0]["price"]
    favorable_move = max(0.0, signed_move if side == "BUY" else -signed_move)
    return _ratio(favorable_move, volume)


def _book_imbalance(tick: Mapping[str, float]) -> float:
    bid, ask = tick.get("bid_qty", 0.0), tick.get("ask_qty", 0.0)
    return _ratio(bid - ask, bid + ask)


def _pivot_break(ticks: Sequence[Mapping[str, float]], direction: str) -> bool:
    if len(ticks) < 8:
        return False
    recent, prior = ticks[-4:], ticks[-8:-4]
    return (max(t["price"] for t in recent) > max(t["price"] for t in prior)
            if direction == "BUY"
            else min(t["price"] for t in recent) < min(t["price"] for t in prior))


def _metrics(ticks: Sequence[Mapping[str, float]]) -> dict[str, float | bool]:
    latest = ticks[-1]
    prices = [t["price"] for t in ticks]
    price_range = max(max(prices) - min(prices), latest["price"] * 0.00005)
    n = max(8, len(ticks) // 3)
    old, new = ticks[:-n], ticks[-n:]
    sell_impact_decay = _ratio(_impact(old, "SELL") - _impact(new, "SELL"), max(_impact(old, "SELL"), 1e-12))
    buy_impact_decay = _ratio(_impact(old, "BUY") - _impact(new, "BUY"), max(_impact(old, "BUY"), 1e-12))
    old_delta, _, _, _ = _flow(old)
    new_delta, _, _, _ = _flow(new)
    buy_flip = new_delta > max(0.08, old_delta + 0.05)
    sell_flip = new_delta < min(-0.08, old_delta - 0.05)

    # Compare terminal fifth with the preceding tape; a sweep must breach and reclaim,
    # not merely have the current price above/below an arbitrary prior extreme.
    recent_count = max(5, len(ticks) // 5)
    reference, recent = ticks[:-recent_count], ticks[-recent_count:]
    if not reference:
        reference = ticks[:-1] or ticks
    ref_low = min(t["price"] for t in reference)
    ref_high = max(t["price"] for t in reference)
    recent_low = min(t["price"] for t in recent)
    recent_high = max(t["price"] for t in recent)
    tolerance = price_range * 0.015
    swept_low = recent_low < ref_low - tolerance
    swept_high = recent_high > ref_high + tolerance
    reclaimed_low = swept_low and latest["price"] >= ref_low
    rejected_high = swept_high and latest["price"] <= ref_high

    mid = max(4, len(ticks) // 2)
    first, second = ticks[:mid], ticks[mid:]
    first_low, first_high = min(t["price"] for t in first), max(t["price"] for t in first)
    second_low, second_high = min(t["price"] for t in second), max(t["price"] for t in second)
    retest_tol = price_range * 0.025
    second_low_fail = first_low - retest_tol <= second_low <= first_low + retest_tol
    second_high_fail = first_high - retest_tol <= second_high <= first_high + retest_tol

    displacement = _ratio(latest["price"] - ticks[-5]["price"], price_range)
    earlier_range = max(ref_high - ref_low, latest["price"] * 0.00005)
    compression_ratio = _ratio(recent_high - recent_low, earlier_range)
    prior_impulse = _ratio(reference[-1]["price"] - reference[0]["price"], earlier_range) if len(reference) >= 2 else 0.0
    book = _book_imbalance(latest)
    return {
        "flow_delta": new_delta, "buy_flip": buy_flip, "sell_flip": sell_flip,
        "sell_impact_decay": sell_impact_decay, "buy_impact_decay": buy_impact_decay,
        "swept_low": swept_low, "reclaimed_low": reclaimed_low,
        "swept_high": swept_high, "rejected_high": rejected_high,
        "second_low_fail": second_low_fail, "second_high_fail": second_high_fail,
        "normalized_displacement": displacement, "book_imbalance": book,
        "compression_ratio": compression_ratio, "prior_impulse": prior_impulse,
        "range": price_range, "ref_low": ref_low, "ref_high": ref_high,
        "low": min(prices), "high": max(prices), "price": latest["price"],
    }


def _score(direction: str, metrics: Mapping[str, Any], pattern: str) -> tuple[int, dict[str, int], list[str]]:
    # Directional evidence: bullish requires sell impact decay; bearish requires buy impact decay.
    flow = bool(metrics["buy_flip"] if direction == "BUY" else metrics["sell_flip"])
    impact = (metrics["sell_impact_decay"] >= 0.25 if direction == "BUY"
               else metrics["buy_impact_decay"] >= 0.25)
    failed_attack = bool(metrics["second_low_fail"] if direction == "BUY" else metrics["second_high_fail"])
    liquidity_failure = bool(metrics["reclaimed_low"] if direction == "BUY" else metrics["rejected_high"])
    book_shift = metrics["book_imbalance"] > 0.12 if direction == "BUY" else metrics["book_imbalance"] < -0.12
    displacement = metrics["normalized_displacement"] > 0.025 if direction == "BUY" else metrics["normalized_displacement"] < -0.025
    pivot = _pivot_break_for_metrics(direction, metrics)
    components = {
        "liquidity_failure": 25 if liquidity_failure else 0,
        "directional_force_decay": 20 if impact else 0,
        "opposite_flow_transition": 20 if flow else 0,
        "repeated_attack_failure": 15 if failed_attack else 0,
        "microstructure_pressure": 10 if book_shift else 0,
        "early_displacement": 10 if pivot or displacement else 0,
    }
    score = sum(components.values())
    if pattern == "SWING_EXHAUSTION":
        gate = liquidity_failure and impact and (flow or failed_attack)
    elif pattern == "COMPRESSION_REVERSAL":
        prior_move_against = metrics["prior_impulse"] <= -0.8 if direction == "BUY" else metrics["prior_impulse"] >= 0.8
        compression = metrics["compression_ratio"] <= 0.45 and prior_move_against
        gate = compression and flow and pivot and failed_attack
    else:
        prior_move_against = metrics["prior_impulse"] <= -0.5 if direction == "BUY" else metrics["prior_impulse"] >= 0.5
        gate = prior_move_against and impact and flow and (pivot or displacement) and abs(metrics["normalized_displacement"]) >= 0.04
    evidence = [name for name, points in components.items() if points]
    if not gate:
        evidence.append("PATTERN_GATE_MISSING")
    return score, components, evidence


def _pivot_break_for_metrics(direction: str, metrics: Mapping[str, Any]) -> bool:
    displacement = metrics["normalized_displacement"]
    return abs(displacement) >= 0.04 and (displacement > 0 if direction == "BUY" else displacement < 0)


def evaluate_vptx(snapshot: Mapping[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
    """Produce six scanner candidates (3 pattern families x BUY/SELL), fail closed."""
    underlying = str(snapshot.get("underlying", "")).upper().strip()
    if underlying not in SUPPORTED_UNDERLYINGS:
        return {"underlying": underlying or None, "status": "REJECTED", "reason": "UNSUPPORTED_UNDERLYING",
                "candidates": [], "scanner_only": True, "order_routing_enabled": False}
    raw_ticks = snapshot.get("ticks")
    ticks = _normalize_ticks(raw_ticks if isinstance(raw_ticks, Sequence) and not isinstance(raw_ticks, (str, bytes)) else [])
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    age = now - ticks[-1]["timestamp_ms"] if ticks else None
    if len(ticks) < MIN_TICKS or age is None or not 0 <= age <= MAX_AGE_MS:
        return {"underlying": underlying, "status": "WAITING_DATA", "tick_count": len(ticks),
                "quote_age_ms": age, "reason": "INSUFFICIENT_OR_STALE_TICKS",
                "candidates": [], "scanner_only": True, "order_routing_enabled": False}
    metrics = _metrics(ticks)
    cross_confirmation = _clamp(_finite(snapshot.get("cross_confirmation")) or 0.0)
    candidates = []
    for pattern in PATTERNS:
        for direction in ("BUY", "SELL"):
            score, components, evidence = _score(direction, metrics, pattern)
            if cross_confirmation >= 0.7:
                score = min(100, score + 5)
                components["cross_market_confirmation"] = 5
            gate = "PATTERN_GATE_MISSING" not in evidence
            ready = gate and score >= 65 and _pivot_break(ticks, direction)
            candidates.append({
                "pattern": pattern, "direction": direction, "score": score,
                "strength": "STRONG" if score >= 75 else "DEVELOPING" if score >= 55 else "WATCH",
                "components": components, "evidence": evidence, "trigger_ready": bool(ready),
                "entry_style": "EARLY_CONTROL_SHIFT" if ready else "WATCH",
                "invalidation_price": metrics["low"] if direction == "BUY" else metrics["high"],
                "target_reference": "VWAP_OR_NEXT_LIQUIDITY_ZONE",
                "scanner_only": True, "order_routing_enabled": False,
            })
    candidates.sort(key=lambda c: (c["trigger_ready"], c["score"]), reverse=True)
    return {"underlying": underlying, "status": "EVALUATED", "tick_count": len(ticks),
            "quote_age_ms": age, "features": metrics, "candidates": candidates,
            "scanner_only": True, "order_routing_enabled": False,
            "subscription_enabled": False, "broker_integration": "NOT_CONFIGURED"}
