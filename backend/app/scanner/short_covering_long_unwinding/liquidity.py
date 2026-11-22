"""Liquidity sweep / reclaim event primitives for Short Covering & Long Unwinding.
Events are candidate context only; adapter must provide synchronized timestamps and levels.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Literal

Side = Literal["BULLISH","BEARISH"]
EventType = Literal["LIQUIDITY_SWEEP","SWEEP_RECLAIM","FAILED_BREAKOUT","REJECTION"]

def _num(v: object) -> float | None:
    if isinstance(v,bool): return None
    try: x=float(v)
    except (TypeError, ValueError, OverflowError): return None
    return x if isfinite(x) else None

@dataclass(frozen=True)
class LiquidityEvent:
    event_type: EventType
    side: Side
    level: float
    observed_price: float
    sweep_distance: float
    timestamp_ms: int
    ttl_ms: int
    confidence: float
    reason: str

def detect_sweep(*, price: object, level: object, side: Side, timestamp_ms: object, min_sweep: object, ttl_ms: int=15_000) -> LiquidityEvent | None:
    p,l,t,m=_num(price),_num(level),_num(timestamp_ms),_num(min_sweep)
    if p is None or l is None or t is None or m is None or m<=0 or ttl_ms<=0 or t<0: return None
    # Bullish setup: price traded below a support/liquidity level then reclaimed it.
    # Bearish setup: price traded above resistance/liquidity then fell back below.
    distance=(l-p) if side=="BULLISH" else (p-l)
    if distance<m: return None
    confidence=min(1.0,max(0.0,distance/(m*3)))
    return LiquidityEvent("LIQUIDITY_SWEEP",side,l,p,distance,int(t),int(ttl_ms),confidence,
      "Downside liquidity swept" if side=="BULLISH" else "Upside liquidity swept")

def detect_reclaim(*, price: object, level: object, side: Side, timestamp_ms: object, sweep: LiquidityEvent, ttl_ms: int=12_000) -> LiquidityEvent | None:
    p,l,t=_num(price),_num(level),_num(timestamp_ms)
    if p is None or l is None or t is None or ttl_ms<=0 or t<sweep.timestamp_ms: return None
    if sweep.side!=side or sweep.event_type!="LIQUIDITY_SWEEP": return None
    reclaimed=(p>l) if side=="BULLISH" else (p<l)
    if not reclaimed or t-sweep.timestamp_ms>sweep.ttl_ms: return None
    return LiquidityEvent("SWEEP_RECLAIM",side,l,p,sweep.sweep_distance,int(t),int(ttl_ms),sweep.confidence,
      "Sweep level reclaimed" if side=="BULLISH" else "Swept level rejected/reclaimed downward")

def event_is_fresh(event: LiquidityEvent, now_ms: object) -> bool:
    now=_num(now_ms)
    return now is not None and now>=event.timestamp_ms and now-event.timestamp_ms<=event.ttl_ms

def detect_cross_sweep(*, previous_price: object, price: object, level: object, side: Side, timestamp_ms: object, min_sweep: object, ttl_ms: int=15_000) -> LiquidityEvent | None:
    """Detect a fresh crossing into liquidity beyond a level (not merely an already-beyond quote)."""
    prev,p,l,t,m=_num(previous_price),_num(price),_num(level),_num(timestamp_ms),_num(min_sweep)
    if prev is None or p is None or l is None or t is None or m is None or m<=0 or ttl_ms<=0 or t<0:
        return None
    crossed=(prev>=l and p<l-m) if side=="BULLISH" else (prev<=l and p>l+m)
    if not crossed: return None
    distance=(l-p) if side=="BULLISH" else (p-l)
    confidence=min(1.0,max(0.0,distance/(m*3)))
    return LiquidityEvent("LIQUIDITY_SWEEP",side,l,p,distance,int(t),int(ttl_ms),confidence,
        "Fresh downside liquidity crossing" if side=="BULLISH" else "Fresh upside liquidity crossing")
