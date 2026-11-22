"""Early Impulse Pro: deterministic market-feature evaluator, scanner scoring and risk gates.

All features are computed from supplied quote/price observations. This module does
not fabricate market inputs, subscribe to feeds, or place broker orders.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence

WEIGHTS = {"absorption": 30, "synthetic_spot": 20, "failed_auction": 15, "market_impact": 20, "cross_instrument": 15}
GRADE_THRESHOLDS = {"A": 85, "B": 75, "WATCH": 65}
TRADE_THRESHOLD = 75


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if isfinite(out) else None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _score(value: Any) -> int:
    numeric = _number(value)
    return int(round(numeric * 100)) if numeric is not None and 0 <= numeric <= 1 else 0


def _direction_from_sign(value: float, epsilon: float = 1e-9) -> str:
    return "BUY" if value > epsilon else "SELL" if value < -epsilon else "NEUTRAL"


def _valid_prices(values: Sequence[Any]) -> list[float]:
    result = []
    for value in values:
        parsed = _number(value)
        if parsed is not None and parsed > 0:
            result.append(parsed)
    return result


def _normalized_range_position(price: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return _clamp((price - low) / (high - low))


def evaluate_early_impulse(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Score normalized engine confidence (0..1) and enforce mandatory gates."""
    raw = snapshot.get("engines") if isinstance(snapshot.get("engines"), Mapping) else {}
    components = {key: _score(raw.get(key)) for key in WEIGHTS}
    weighted = round(sum(components[key] * weight / 100 for key, weight in WEIGHTS.items()))
    direction = str(snapshot.get("direction", "NEUTRAL")).upper()
    if direction not in {"BUY", "SELL", "NEUTRAL"}:
        direction = "NEUTRAL"
    evidence = snapshot.get("engine_evidence") if isinstance(snapshot.get("engine_evidence"), Mapping) else {}
    engine_evidence = {key: evidence.get(key) is True for key in WEIGHTS}
    engine_evidence_complete = all(engine_evidence.values())
    gates = {
        "data_valid": snapshot.get("data_valid") is True,
        "spread_ok": snapshot.get("spread_ok") is True,
        "entry_not_extended": snapshot.get("entry_not_extended") is True,
        "invalidation_defined": snapshot.get("invalidation_defined") is True,
        "no_strong_opposite": snapshot.get("no_strong_opposite") is True,
        "engine_evidence_complete": engine_evidence_complete,
    }
    passed = all(gates.values())
    grade = "A" if weighted >= 85 else "B" if weighted >= 75 else "WATCH" if weighted >= 65 else "REJECT"
    eligible = passed and direction in {"BUY", "SELL"} and weighted >= TRADE_THRESHOLD
    reasons = [key.upper() for key, ok in gates.items() if not ok]
    if direction == "NEUTRAL":
        reasons.append("NO_DIRECTION")
    if weighted < TRADE_THRESHOLD:
        reasons.append("SCORE_BELOW_TRADE_THRESHOLD")
    return {
        "strategy": "EARLY_IMPULSE_PRO", "direction": direction, "score": weighted,
        "grade": grade, "eligible": eligible, "components": components,
        "weights": dict(WEIGHTS), "gates": gates, "engine_evidence": engine_evidence,
        "engine_evidence_complete": engine_evidence_complete, "rejection_reasons": reasons,
        "mode": "SCANNER_ONLY", "order_routing_enabled": False,
    }


def evaluate_market_features(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate the five signal engines from quote history and synchronized instruments.

    Expected fields:
      prices: chronological positive LTP list; volumes: same-length volume deltas
      bid_qty, ask_qty: current displayed depth; future_prices, spot_prices:
      synchronized chronological series; reference_high/low: auction levels.
      current_price, bid, ask, quote_age_seconds, feed_authenticated, cross_prices:
      other synchronized instruments (e.g. confirming indices).
    Missing/invalid evidence makes relevant engines neutral and gates fail closed.
    """
    prices = _valid_prices(snapshot.get("prices", []))
    volumes = [_number(v) for v in snapshot.get("volumes", [])]
    current = _number(snapshot.get("current_price")) or (prices[-1] if prices else None)
    bid, ask = _number(snapshot.get("bid")), _number(snapshot.get("ask"))
    bid_qty, ask_qty = _number(snapshot.get("bid_qty")), _number(snapshot.get("ask_qty"))
    price_move = (prices[-1] - prices[0]) / prices[0] if len(prices) >= 2 else 0.0
    raw_price_count = len(snapshot.get("prices", []))
    raw_volume_count = len(snapshot.get("volumes", []))
    valid_volume_samples = [v for v in volumes if v is not None and v >= 0]
    prices_valid = len(prices) == raw_price_count
    depth_valid = bid_qty is not None and ask_qty is not None and bid_qty >= 0 and ask_qty >= 0 and bid_qty + ask_qty > 0
    absorption_evidence = depth_valid and prices_valid and len(prices) >= 2
    market_impact_evidence = prices_valid and len(prices) >= 2 and raw_volume_count == raw_price_count and len(valid_volume_samples) >= 2 and sum(valid_volume_samples) > 0
    imbalance = ((bid_qty - ask_qty) / (bid_qty + ask_qty)) if depth_valid else 0.0

    # Absorption: positive depth imbalance with little adverse price movement, or
    # price rising while offered size is consumed; inverse for bearish pressure.
    response = price_move * 10000
    absorption_raw = _clamp((abs(imbalance) * 0.65 + min(abs(response), 8) / 8 * 0.35)) if absorption_evidence else 0.0
    absorption_dir = _direction_from_sign(imbalance if abs(imbalance) >= 0.08 else price_move) if absorption_evidence else "NEUTRAL

    fut = _valid_prices(snapshot.get("future_prices", []))
    spot = _valid_prices(snapshot.get("spot_prices", []))
    basis_change = 0.0
    basis_dir = "NEUTRAL"
    basis_score = 0.0
    if len(fut) >= 2 and len(spot) >= 2:
        paired = min(len(fut), len(spot))
        basis0 = fut[-paired] - spot[-paired]
        basis1 = fut[-1] - spot[-1]
        basis_change = basis1 - basis0
        fut_move = (fut[-1] - fut[-paired]) / fut[-paired]
        spot_move = (spot[-1] - spot[-paired]) / spot[-paired]
        lead_lag = fut_move - spot_move
        basis_dir = _direction_from_sign(fut_move + spot_move)
        basis_score = _clamp(abs(lead_lag) * 5000 + min(abs(fut_move + spot_move) * 10000, 1) * 0.5)
    synthetic_evidence = len(fut) >= 2 and len(spot) >= 2
    synthetic_confidence = basis_score if synthetic_evidence else 0.0
    if not synthetic_evidence:
        basis_dir = "NEUTRAL

    # Failed auction/rejection: close near one edge then reverse inside the prior
    # range. No auction signal without explicit levels and at least 3 prices.
    ref_high, ref_low = _number(snapshot.get("reference_high")), _number(snapshot.get("reference_low"))
    failed_auction_evidence = prices_valid and len(prices) >= 3 and ref_high is not None and ref_low is not None and ref_high > ref_low > 0
    failed_dir, failed_score = "NEUTRAL", 0.0
    if failed_auction_evidence:
        last, prior = prices[-1], prices[-2]
        if prior > ref_high and last < ref_high:
            failed_dir, failed_score = "SELL", _clamp((prior - last) / max(prior - ref_low, 1e-9) * 4)
        elif prior < ref_low and last > ref_low:
            failed_dir, failed_score = "BUY", _clamp((last - prior) / max(ref_high - prior, 1e-9) * 4)

    # Market impact: directional move per unit volume, capped and normalized.
    recent_vol = [v for v in volumes[-len(prices):] if v is not None and v > 0] if prices else []
    impact_dir = _direction_from_sign(price_move) if market_impact_evidence else "NEUTRAL"
    impact_conf = _clamp(abs(price_move) * 10000) if recent_vol and market_impact_evidence else 0.0
    if len(prices) >= 2 and len(volumes) >= 2:
        valid_vol = [_number(v) for v in volumes[-len(prices):]]
        total_vol = sum(v for v in valid_vol if v is not None and v >= 0)
        if total_vol > 0:
            impact_conf = _clamp(abs(price_move) * 10000 / max(total_vol / max(len(prices), 1), 1) * 100)

    # Cross-instrument: direction must agree across at least one independent,
    # synchronized symbol; otherwise score zero or penalize conflict.
    cross = [_number(v) for v in snapshot.get("cross_prices", [])]
    cross_moves = [((cross[i] - cross[i-1]) / cross[i-1]) for i in range(1, len(cross)) if cross[i] is not None and cross[i-1] is not None and cross[i-1] > 0]
    cross_evidence = len(cross) >= 2
    cross_dir = _direction_from_sign(sum(cross_moves)) if cross_moves and cross_evidence else "NEUTRAL"
    cross_conf = _clamp(abs(sum(cross_moves)) * 10000) if cross_moves and cross_evidence else 0.0
    if cross_evidence and absorption_evidence and cross_dir != "NEUTRAL" and cross_dir != absorption_dir:
        cross_conf *= 0.25

    engine_evidence = {
        "absorption": absorption_evidence,
        "synthetic_spot": synthetic_evidence,
        "failed_auction": failed_auction_evidence,
        "market_impact": market_impact_evidence,
        "cross_instrument": cross_evidence,
    }
    if not market_impact_evidence:
        impact_conf = 0.0
    engines = {
        "absorption": absorption_raw,
        "synthetic_spot": synthetic_confidence,
        "failed_auction": failed_score,
        "market_impact": impact_conf,
        "cross_instrument": cross_conf,
    }
    directional_votes = [
        d for key, d in (("absorption", absorption_dir), ("synthetic_spot", basis_dir),
            ("failed_auction", failed_dir), ("market_impact", impact_dir), ("cross_instrument", cross_dir))
        if engine_evidence[key] and d in {"BUY", "SELL"}
    ]
    buys, sells = directional_votes.count("BUY"), directional_votes.count("SELL")
    direction = "BUY" if buys > sells else "SELL" if sells > buys else "NEUTRAL"
    opposite = max(buys, sells) >= 3 and min(buys, sells) >= 2

    spread_pct = ((ask - bid) / current * 100) if current and bid and ask and 0 < bid <= ask else None
    age = _number(snapshot.get("quote_age_seconds"))
    data_valid = (snapshot.get("feed_authenticated") is True and age is not None and 0 <= age <= 2.0 and prices_valid and len(prices) >= 3 and current is not None)
    spread_ok = spread_pct is not None and spread_pct <= 0.15
    recent_high, recent_low = (max(prices[-10:]), min(prices[-10:])) if prices else (None, None)
    extension = ((current - recent_low) / max(recent_high - recent_low, 1e-9)) if current and recent_high and recent_low else 0.5
    entry_not_extended = (extension <= 0.85 if direction == "BUY" else extension >= 0.15 if direction == "SELL" else False)
    invalidation = (recent_low if direction == "BUY" else recent_high if direction == "SELL" else None)
    gates_input = {
        "engines": engines, "direction": direction, "data_valid": data_valid,
        "spread_ok": spread_ok, "entry_not_extended": entry_not_extended,
        "invalidation_defined": invalidation is not None and invalidation > 0,
        "no_strong_opposite": not opposite, "engine_evidence": engine_evidence,
    }
    result = evaluate_early_impulse(gates_input)
    result.update({
        "features": {"price_move_pct": round(price_move * 100, 5), "depth_imbalance": round(imbalance, 4),
            "basis_change": round(basis_change, 4), "spread_pct": round(spread_pct, 5) if spread_pct is not None else None,
            "cross_direction": cross_dir, "auction_rejection_direction": failed_dir,
            "engine_evidence": dict(engine_evidence)},
        "entry": current if result["eligible"] else None,
        "invalidation": invalidation if result["eligible"] else None,
        "target_1": (current + (current - invalidation) if direction == "BUY" else current - (invalidation - current)) if result["eligible"] and invalidation is not None else None,
        "risk_reward": 1.0 if result["eligible"] else None,
        "data_valid": data_valid,
    })
    return result
