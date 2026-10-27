"""Low-latency WebSocket fan-out for Box Spread scanner results."""
import asyncio
from datetime import datetime, timezone
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["box-spread"])
_subscribers = set()
_loop = None
_latest_getter = None
_expiry_types = {}

def configure(getter, expiry_types_getter=None):
    global _latest_getter
    _latest_getter = getter

def set_expiry_types(mapping):
    global _expiry_types
    _expiry_types = dict(mapping or {})

def _row(result):
    low, high = result.low, result.high
    now_ns = time.time_ns()
    return {
        "symbol": low.underlying, "instrument_class": low.instrument_class,
        "expiry": low.expiry, "low_strike": low.strike, "high_strike": high.strike,
        "direction": result.direction, "timestamp_ns": int(low.timestamp_ns),
        "high_timestamp_ns": int(high.timestamp_ns),
        "last_update_timestamp_ns": max(int(low.timestamp_ns), int(high.timestamp_ns)),
        "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "freshness": "LIVE",
        "quote_age_seconds": round(max(0.0, (now_ns - max(int(low.timestamp_ns), int(high.timestamp_ns))) / 1e9), 3),
        "low_call_ltp": getattr(low, "call_ltp", None), "low_call_bid": low.call_bid, "low_call_ask": low.call_ask,
        "low_put_ltp": getattr(low, "put_ltp", None), "low_put_bid": low.put_bid, "low_put_ask": low.put_ask,
        "high_call_ltp": getattr(high, "call_ltp", None), "high_call_bid": high.call_bid, "high_call_ask": high.call_ask,
        "high_put_ltp": getattr(high, "put_ltp", None), "high_put_bid": high.put_bid, "high_put_ask": high.put_ask,
        "executable_edge": result.executable_edge, "edge_per_lot": result.edge_per_lot,
        "gross_pnl": result.gross_pnl, "lot_size": low.lot_size, "strike_distance": result.strike_distance,
    }

def publish(results):
    """Call from scanner callback threads; queues are touched on the event loop only."""
    loop = _loop
    if loop is None or not loop.is_running():
        return
    rows = []
    for result in results or ():
        try:
            row = _row(result)
            symbol = str(row["symbol"]).upper()
            threshold = 5.0 if symbol == "NIFTY" else 10.0
            # The stream threshold is strict, independently of scanner config.
            if float(row["executable_edge"]) > threshold:
                rows.append(row)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
    if not rows:
        return
    message = {"type": "opportunities", "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": rows}
    def deliver():
        for queue in tuple(_subscribers):
            if queue.full():
                try: queue.get_nowait()
                except asyncio.QueueEmpty: pass
            try: queue.put_nowait(message)
            except asyncio.QueueFull: pass
    loop.call_soon_threadsafe(deliver)

@router.websocket("/ws/live-opportunities")
async def live_opportunities(websocket: WebSocket):
    global _loop
    await websocket.accept()
    _loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=100)
    _subscribers.add(queue)
    try:
        await websocket.send_json({"type": "connected", "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        if callable(_latest_getter):
            try:
                current = [_row(item) for item in (_latest_getter() or ())]
                current = [r for r in current if float(r.get("executable_edge") or 0) > (5.0 if str(r.get("symbol")).upper() == "NIFTY" else 10.0)]
                if current:
                    await websocket.send_json({"type": "opportunities", "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": current})
            except Exception:
                pass
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.discard(queue)
        if not _subscribers:
            _loop = None
