"""Derived IV for shared index market data; explicitly tagged as model-calculated."""
from __future__ import annotations
from collections import deque
from datetime import datetime, time
from math import exp, log, sqrt, erf
from threading import RLock
from time import time_ns
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RATE = 0.065

def _cdf(x):
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))

def _bs(s, k, t, v, call):
    if t <= 0 or v <= 0:
        return max(s-k, 0.0) if call else max(k-s, 0.0)
    vt = v * sqrt(t)
    d1 = (log(s/k) + (RATE + 0.5*v*v)*t) / vt
    d2 = d1-vt
    disc = exp(-RATE*t)
    return s*_cdf(d1)-k*disc*_cdf(d2) if call else k*disc*_cdf(-d2)-s*_cdf(-d1)

def implied_volatility(price, spot, strike, expiry, option_type, *, now=None):
    """Annualized IV in percent, or None if input/price is not invertible."""
    try:
        p, s, k = float(price), float(spot), float(strike)
        current = (now or datetime.now(IST)).astimezone(IST)
        expiry_date = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
        expiry_dt = datetime.combine(expiry_date, time(15, 30), tzinfo=IST)
        t = (expiry_dt-current).total_seconds()/(365*24*3600)
        call = str(option_type).upper() in ("CE", "CALL")
        put = str(option_type).upper() in ("PE", "PUT")
        if not (call or put) or t <= 0 or p <= 0 or s <= 0 or k <= 0:
            return None
        discounted_k = k*exp(-RATE*t)
        intrinsic = max(s-discounted_k, 0.0) if call else max(discounted_k-s, 0.0)
        upper = s if call else discounted_k
        if p <= intrinsic+1e-8 or p >= upper-1e-8:
            return None
        low, high = 0.01, 5.0
        if _bs(s,k,t,high,call) < p:
            return None
        for _ in range(80):
            mid=(low+high)/2
            if _bs(s,k,t,mid,call) < p: low=mid
            else: high=mid
        iv=(low+high)/2*100
        return iv if 1 <= iv <= 500 else None
    except (ValueError, TypeError, OverflowError):
        return None

class SharedIndexDerivedIV:
    """Bounded per-token history for derived IV and 1-second sampled change."""
    def __init__(self):
        self._lock=RLock()
        self._history={}

    def decorate(self, records, spots):
        now=time_ns()
        out=[]
        with self._lock:
            active=set()
            for record in records:
                row=dict(record)
                key=f"{row.get('exchange')}:{row.get('segment')}:{row.get('token')}"
                active.add(key)
                iv=None
                if row.get("instrument_type")=="option" and row.get("fresh"):
                    spot=spots.get(str(row.get("underlying") or "").upper())
                    if spot is not None and row.get("ltp") is not None:
                        iv=implied_volatility(row["ltp"],spot,row.get("strike"),row.get("expiry"),row.get("option_type"))
                row["iv_percent"]=iv
                row["iv_source"]="derived_bsm" if iv is not None else None
                row["iv_delta_1s"]=None
                if iv is not None:
                    history=self._history.setdefault(key,deque(maxlen=12))
                    ts=row.get("timestamp_ns",now)
                    if not history or ts>history[-1][0]: history.append((ts,iv))
                    while history and now-history[0][0]>5_000_000_000: history.popleft()
                    baseline=next((value for stamp,value in history if stamp<=ts-1_000_000_000),None)
                    if baseline is not None: row["iv_delta_1s"]=round(iv-baseline,4)
                out.append(row)
            for key in tuple(self._history):
                if key not in active: self._history.pop(key,None)
        return out

    def clear(self):
        with self._lock: self._history.clear()
