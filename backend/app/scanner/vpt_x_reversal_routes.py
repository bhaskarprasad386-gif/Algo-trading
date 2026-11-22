"""Isolated VPT-X reversal API. Snapshot-in/evaluate only; no live feed wiring."""
from __future__ import annotations
import threading
import time
from collections import OrderedDict
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.scanner.vpt_x_reversal_engine import evaluate_vptx, SUPPORTED_UNDERLYINGS

router = APIRouter(prefix="/api/v1/scanner/vpt-x", tags=["vpt-x-reversal"])
_lock = threading.RLock()
_history: OrderedDict[str, dict[str, Any]] = OrderedDict()
_CAPACITY = 300
_TTL_SECONDS = 180
_DUPLICATE_WINDOW_SECONDS = 20
_SIGNAL_EXPIRE_SECONDS = 45

class SnapshotRequest(BaseModel):
    underlying: str
    ticks: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    cross_confirmation: float | None = Field(default=None, ge=0, le=1)

def _now_ms() -> int:
    return int(time.time() * 1000)

def _cleanup(now: int) -> None:
    cutoff = now - _TTL_SECONDS * 1000
    expire_before = now - _SIGNAL_EXPIRE_SECONDS * 1000
    for key, item in list(_history.items()):
        updated = int(item.get("_updated_ms", 0))
        if updated < cutoff:
            _history.pop(key, None)
        elif updated < expire_before and item.get("status") in {"WATCH", "TRIGGERED"}:
            item["status"] = "EXPIRED"
            item["_expired_ms"] = now
    while len(_history) > _CAPACITY:
        _history.popitem(last=False)

@router.get("/status")
def status():
    with _lock:
        _cleanup(_now_ms())
        count = len(_history)
    return {"scanner": "VPT-X", "status": "ISOLATED", "underlyings": sorted(SUPPORTED_UNDERLYINGS),
            "patterns": ["SWING_EXHAUSTION", "COMPRESSION_REVERSAL", "V_REVERSAL"],
            "directions": ["BUY", "SELL"], "signal_count": count,
            "feed_connected": False, "scanner_active": False,
            "feed_integration": "NOT_CONNECTED", "scanner_only": True,
            "order_routing_enabled": False, "paper_execution_enabled": False}

@router.post("/evaluate")
def evaluate(payload: SnapshotRequest):
    symbol = payload.underlying.strip().upper()
    if symbol not in SUPPORTED_UNDERLYINGS:
        raise HTTPException(status_code=422, detail="underlying must be NIFTY or BANKNIFTY")
    snapshot = payload.model_dump()
    snapshot["underlying"] = symbol
    result = evaluate_vptx(snapshot, now_ms=_now_ms())
    if result.get("status") != "EVALUATED":
        return result
    now = _now_ms()
    active_keys: set[str] = set()
    candidates = []
    with _lock:
        _cleanup(now)
        latest_price = float(result["features"]["price"])
        for candidate in result["candidates"]:
            key = f"{symbol}:{candidate['pattern']}:{candidate['direction']}"
            active_keys.add(key)
            prior = _history.get(key)
            state = "TRIGGERED" if candidate["trigger_ready"] else "WATCH"
            invalidation = candidate.get("invalidation_price")
            if prior and prior.get("status") in {"TRIGGERED", "WATCH", "INVALIDATED", "EXPIRED"}:
                prev_state = prior.get("status")
                if prev_state == "TRIGGERED":
                    invalid = (latest_price < invalidation) if candidate["direction"] == "BUY" else (latest_price > invalidation)
                    state = "INVALIDATED" if invalid else "TRIGGERED"
                elif prior.get("status") == "INVALIDATED":
                    state = "WATCH" if not candidate["trigger_ready"] else "INVALIDATED"
                elif prior.get("status") == "EXPIRED":
                    state = "WATCH" if not candidate["trigger_ready"] else "TRIGGERED"
                elif state == "TRIGGERED":
                    state = "TRIGGERED"
            item = {**candidate, "underlying": symbol, "status": state,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now / 1000)),
                    "last_price": latest_price, "scanner_only": True,
                    "order_routing_enabled": False}
            # Preserve signal identity/time for same live trigger; otherwise create a new event.
            if prior and prior.get("status") == state and now - int(prior.get("_updated_ms", 0)) <= _DUPLICATE_WINDOW_SECONDS * 1000:
                item["signal_id"] = prior.get("signal_id")
                item["created_at"] = prior.get("created_at")
            else:
                item["signal_id"] = f"{key}:{now}"
                item["created_at"] = item["updated_at"]
            item["_updated_ms"] = now
            _history[key] = item
            _history.move_to_end(key)
            candidates.append({k: v for k, v in item.items() if not k.startswith("_")})
        _cleanup(now)
    return {**result, "candidates": candidates}

@router.get("/signals")
def signals(underlying: str | None = None, limit: int = 100):
    limit = max(1, min(int(limit), 200))
    symbol = (underlying or "").strip().upper()
    if symbol and symbol not in SUPPORTED_UNDERLYINGS:
        raise HTTPException(status_code=422, detail="underlying must be NIFTY or BANKNIFTY")
    with _lock:
        _cleanup(_now_ms())
        items = [dict(v) for v in reversed(_history.values()) if not symbol or v.get("underlying") == symbol]
    items = [{k: v for k, v in row.items() if not k.startswith("_")} for row in items[:limit]]
    return {"signals": items, "count": len(items), "capacity": _CAPACITY,
            "scanner_only": True, "order_routing_enabled": False}
