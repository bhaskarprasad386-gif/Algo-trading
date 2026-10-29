"""Low-latency WebSocket fan-out for Box Spread scanner results."""
import asyncio
from datetime import datetime, timezone
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["box-spread"])
_subscribers = set()
_loop = None
_latest_getter = None
_spot_provider = None
_expiry_types = {}
MAX_QUOTE_AGE_NS = 5_000_000_000
MAX_LEG_TIMESTAMP_SKEW_NS = 3_000_000_000


def configure(getter, expiry_types_getter=None):
    global _latest_getter
    _latest_getter = getter


def configure_spot_provider(provider):
    """Configure a source-backed, freshness-checked underlying spot getter."""
    global _spot_provider
    _spot_provider = provider


def _spot_fields(symbol):
    snapshot = _spot_provider(symbol) if callable(_spot_provider) else None
    if not isinstance(snapshot, dict):
        return {"spot_price": None, "spot_timestamp_ns": None, "spot_age_seconds": None}
    return {
        "spot_price": snapshot.get("spot_price"),
        "spot_timestamp_ns": snapshot.get("spot_timestamp_ns"),
        "spot_age_seconds": snapshot.get("spot_age_seconds"),
    }


def _expiry_key(value):
    from datetime import datetime
    text = str(value or "").strip().upper()
    for fmt in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return "".join(character for character in text if character.isalnum())


def set_expiry_types(mapping):
    global _expiry_types
    _expiry_types = {
        (str(symbol).strip().upper(), _expiry_key(expiry)): str(kind).strip().upper()
        for (symbol, expiry), kind in (mapping or {}).items()
    }


def _leg_timestamps(low, high):
    """Return timestamps for all four executable option legs."""
    low_ts = int(getattr(low, "timestamp_ns", 0) or 0)
    high_ts = int(getattr(high, "timestamp_ns", 0) or 0)
    return (
        int(getattr(low, "call_timestamp_ns", None) or low_ts),
        int(getattr(low, "put_timestamp_ns", None) or low_ts),
        int(getattr(high, "call_timestamp_ns", None) or high_ts),
        int(getattr(high, "put_timestamp_ns", None) or high_ts),
    )


def _row(result):
    low, high = result.low, result.high
    now_ns = time.time_ns()
    leg_timestamps = _leg_timestamps(low, high)
    oldest_timestamp = min(leg_timestamps)
    newest_timestamp = max(leg_timestamps)

    # Keep the stream's freshness contract aligned with the HTTP live endpoint.
    if not all(now_ns - MAX_QUOTE_AGE_NS <= stamp <= now_ns for stamp in leg_timestamps):
        return None
    if newest_timestamp - oldest_timestamp > MAX_LEG_TIMESTAMP_SKEW_NS:
        return None

    return {
        "symbol": low.underlying, "instrument_class": low.instrument_class,
        "expiry": low.expiry, "expiry_type": _expiry_types.get((str(low.underlying).strip().upper(), _expiry_key(low.expiry)), "UNKNOWN"), "low_strike": low.strike, "high_strike": high.strike,
        "direction": result.direction, **_spot_fields(low.underlying), "timestamp_ns": int(low.timestamp_ns),
        "high_timestamp_ns": int(high.timestamp_ns),
        "last_update_timestamp_ns": newest_timestamp,
        "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "freshness": "LIVE",
        "quote_age_seconds": round(max(0.0, (now_ns - oldest_timestamp) / 1e9), 3),
        "timestamp_skew_ms": round((newest_timestamp - oldest_timestamp) / 1e6, 3),
        "low_call_ltp": getattr(low, "call_ltp", None), "low_call_bid": low.call_bid, "low_call_ask": low.call_ask,
        "low_put_ltp": getattr(low, "put_ltp", None), "low_put_bid": low.put_bid, "low_put_ask": low.put_ask,
        "high_call_ltp": getattr(high, "call_ltp", None), "high_call_bid": high.call_bid, "high_call_ask": high.call_ask,
        "high_put_ltp": getattr(high, "put_ltp", None), "high_put_bid": high.put_bid, "high_put_ask": high.put_ask,
        "executable_edge": result.executable_edge, "edge_per_lot": result.edge_per_lot,
        "gross_pnl": result.gross_pnl, "lot_size": low.lot_size, "strike_distance": result.strike_distance,
    }


def _passes_edge_threshold(row):
    symbol = str(row["symbol"]).upper()
    threshold = 5.0 if symbol == "NIFTY" else 10.0
    return float(row["executable_edge"]) > threshold


def publish(results):
    """Call from scanner callback threads; queues are touched on the event loop only."""
    loop = _loop
    if loop is None or not loop.is_running():
        return
    rows = []
    for result in results or ():
        try:
            row = _row(result)
            if row is not None and _passes_edge_threshold(row):
                rows.append(row)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
    if not rows:
        return
    message = {"type": "opportunities", "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": rows}

    def deliver():
        for queue in tuple(_subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

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
                current = []
                for item in (_latest_getter() or ()):
                    row = _row(item)
                    if row is not None and _passes_edge_threshold(row):
                        current.append(row)
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
