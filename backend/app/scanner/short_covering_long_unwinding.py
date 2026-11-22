"""Short Covering & Long Unwinding: independent directional evidence aggregator."""
from __future__ import annotations
from math import isfinite
from typing import Any, Mapping
from .short_covering_long_unwinding.features import robust_z

GROUP_WEIGHTS = {"liquidity": .20, "order_flow": .20, "futures": .20, "options": .12, "structure": .18, "participation": .10}
CONFLICT_LIMIT = .22

def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: result = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return result if isfinite(result) else None

def _score(value: Any) -> float | None:
    x = _finite_float(value)
    return x if x is not None and -1 <= x <= 1 else None

def evaluate_signal(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    raw = snapshot.get("groups") if isinstance(snapshot.get("groups"), Mapping) else {}
    groups = {}; evidence = {}; bull = bear = weight_sum = 0.0
    for name, weight in GROUP_WEIGHTS.items():
        value = _score(raw.get(name)); groups[name] = value
        if value is None: evidence[name] = "MISSING"; continue
        weight_sum += weight; bull += weight * max(0, value); bear += weight * max(0, -value)
        evidence[name] = "BULLISH" if value > .15 else "BEARISH" if value < -.15 else "NEUTRAL"
    coverage = weight_sum / sum(GROUP_WEIGHTS.values())
    bscore = round(100*bull/weight_sum) if weight_sum else 0
    dscore = round(100*bear/weight_sum) if weight_sum else 0
    conflict = round(100*min(bull,bear)/max(bull+bear,1e-9))
    q = snapshot.get("quality") if isinstance(snapshot.get("quality"), Mapping) else {}
    gates = {k: q.get(k) is True for k in ("fresh_feed","synchronized","execution_quality","regime_known")}
    side = "BULLISH" if bscore>dscore else "BEARISH" if dscore>bscore else "NONE"
    conf = max(bscore,dscore)
    state = "DATA_INSUFFICIENT" if coverage < .60 else "CONFLICT" if conflict >= 100*CONFLICT_LIMIT else "PRE_IMPULSE" if conf >= 65 else "PRESSURE_BUILDING" if conf >= 45 else "IDLE"
    eligible = state == "PRE_IMPULSE" and all(gates.values()) and side != "NONE"
    return {"strategy":"SHORT_COVERING_LONG_UNWIND","side":side,"bullish_score":bscore,"bearish_score":dscore,
      "conflict_score":conflict,"coverage":round(coverage,3),"group_evidence":evidence,
      "state":state,"alert_eligible":eligible,"quality_gates":gates,"order_routing_enabled":False}
