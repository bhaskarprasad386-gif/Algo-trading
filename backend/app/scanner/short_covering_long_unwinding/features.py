"""Feature normalization primitives for the Short Covering & Long Unwinding engine.
Only synchronized, timestamped inputs should be passed by the upstream adapter.
"""
from __future__ import annotations
from math import isfinite
from statistics import median
from typing import Sequence

MIN_HISTORY = 8
MAD_SCALE = 1.4826
ZSCORE_CLIP = 5.0

def _finite(value: object) -> float | None:
    if isinstance(value, bool): return None
    try: v = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return v if isfinite(v) else None

def robust_z(value: object, history: Sequence[object], *, min_history: int = MIN_HISTORY) -> float | None:
    current = _finite(value)
    clean = [v for raw in history if (v := _finite(raw)) is not None]
    if current is None or len(clean) < min_history: return None
    center = median(clean)
    mad = median([abs(x-center) for x in clean])
    if mad <= 0: return None
    return max(-ZSCORE_CLIP, min(ZSCORE_CLIP, (current-center)/(MAD_SCALE*mad)))

def normalized_momentum(current: object, previous: object, scale: object) -> float | None:
    c, p, s = _finite(current), _finite(previous), _finite(scale)
    if c is None or p is None or s is None or s <= 0: return None
    raw=(c-p)/s
    if not isfinite(raw): return None
    return max(-1.0,min(1.0,raw))

def directional_pressure(*, positive: object = None, negative: object = None) -> tuple[float | None,float | None]:
    """Map independent evidence to directional bounded scores; absent remains absent."""
    p,n=_finite(positive),_finite(negative)
    return (max(0,min(1,p)) if p is not None else None,
            max(0,min(1,n)) if n is not None else None)
