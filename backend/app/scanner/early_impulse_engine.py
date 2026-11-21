"""Early Impulse Pro: independent non-IV scanner scoring and risk gates.

This module is a pure evaluator. It does not subscribe to market data, place orders,
or depend on the existing IV scanner.
"""
from __future__ import annotations

from typing import Any, Mapping

WEIGHTS = {
    "absorption": 30,
    "synthetic_spot": 20,
    "failed_auction": 15,
    "market_impact": 20,
    "cross_instrument": 15,
}

GRADE_THRESHOLDS = {"A": 85, "B": 75, "WATCH": 65}


def _score(value: Any) -> int:
    """Convert normalized 0..1 engine confidence to a weighted integer score."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    if not 0 <= numeric <= 1:
        return 0
    return int(round(numeric * 100))


def evaluate_early_impulse(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate an externally assembled normalized snapshot; no live-feed claims.

    Required gates fail closed. Engine inputs are normalized confidence values
    (0..1), not raw quantities. This intentionally does not infer missing data.
    """
    raw_engines = snapshot.get("engines") if isinstance(snapshot.get("engines"), Mapping) else {}
    components = {
        key: _score(raw_engines.get(key))
        for key in WEIGHTS
    }
    weighted = round(sum(components[key] * weight / 100 for key, weight in WEIGHTS.items()))
    direction = str(snapshot.get("direction", "NEUTRAL")).upper()
    if direction not in {"BUY", "SELL", "NEUTRAL"}:
        direction = "NEUTRAL"

    gates = {
        "data_valid": snapshot.get("data_valid") is True,
        "spread_ok": snapshot.get("spread_ok") is True,
        "entry_not_extended": snapshot.get("entry_not_extended") is True,
        "invalidation_defined": snapshot.get("invalidation_defined") is True,
        "no_strong_opposite": snapshot.get("no_strong_opposite") is True,
    }
    passed = all(gates.values())
    grade = "A" if weighted >= GRADE_THRESHOLDS["A"] else "B" if weighted >= GRADE_THRESHOLDS["B"] else "WATCH" if weighted >= GRADE_THRESHOLDS["WATCH"] else "REJECT"
    eligible = passed and direction in {"BUY", "SELL"} and weighted >= GRADE_THRESHOLDS["B"]
    reasons = [name.upper() for name, ok in gates.items() if not ok]
    if direction == "NEUTRAL":
        reasons.append("NO_DIRECTION")
    if weighted < GRADE_THRESHOLDS["B"]:
        reasons.append("SCORE_BELOW_TRADE_THRESHOLD")

    return {
        "strategy": "EARLY_IMPULSE_PRO",
        "direction": direction,
        "score": weighted,
        "grade": grade,
        "eligible": eligible,
        "components": components,
        "weights": dict(WEIGHTS),
        "gates": gates,
        "rejection_reasons": reasons,
        "mode": "SCANNER_ONLY",
        "order_routing_enabled": False,
    }
