"""HTTP access to live Box Spread opportunities and durable alerts."""
from typing import Annotated
import json
from fastapi import APIRouter, Query
import time
from app.models import LiveBoxSpreadAlertHistory, LiveBoxSpreadSnapshot
from app.core.database import SessionLocal

router=APIRouter(prefix="/api/v1/scanner/box-spread",tags=["box-spread"])
_latest=()
_expiry_types = {}
MAX_LEG_TIMESTAMP_SKEW_NS = 3_000_000_000

def _expiry_date(value):
    from datetime import datetime
    text = str(value or "").strip().upper()
    for fmt in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d %B %Y", "%d %B %y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def _expiry_key(value):
    """Canonicalize expiry representations across instrument-master and API formats."""
    parsed = _expiry_date(value)
    if parsed:
        return parsed
    return "".join(character for character in str(value or "").strip().upper() if character.isalnum())

def configure(getter):
    global _latest; _latest=getter

def set_expiry_types(expiry_types):
    """Set expiry classifications derived from the current option instrument master."""
    global _expiry_types
    _expiry_types = {
        (str(symbol).strip().upper(), _expiry_key(expiry)): str(kind).strip().upper()
        for (symbol, expiry), kind in (expiry_types or {}).items()
    }

@router.get("/live")
def live(limit: Annotated[int, Query(ge=1,le=500)] = 50, max_age_seconds: Annotated[float, Query(gt=0,le=60)] = 5.0):
    rows=list(_latest()) if callable(_latest) else list(_latest)
    now_ns=time.time_ns()
    max_age_ns=int(max_age_seconds*1_000_000_000)
    cutoff=now_ns-max_age_ns
    max_skew_ns=min(max_age_ns,MAX_LEG_TIMESTAMP_SKEW_NS)
    fresh_rows=[]
    for row in rows:
        low_ts=int(getattr(row.low,"timestamp_ns",0) or 0)
        high_ts=int(getattr(row.high,"timestamp_ns",0) or 0)
        leg_timestamps = (
            int(getattr(row.low, "call_timestamp_ns", None) or low_ts),
            int(getattr(row.low, "put_timestamp_ns", None) or low_ts),
            int(getattr(row.high, "call_timestamp_ns", None) or high_ts),
            int(getattr(row.high, "put_timestamp_ns", None) or high_ts),
        )
        # All four executable option legs must be fresh and synchronized.
        if not all(cutoff <= stamp <= now_ns for stamp in leg_timestamps):
            continue
        if max(leg_timestamps) - min(leg_timestamps) > max_skew_ns:
            continue
        fresh_rows.append(row)
    rows=fresh_rows[:limit]
    return {"strategy":"box-spread","mode":"paper-safe","count":len(rows),"opportunity_count":sum(float(r.executable_edge or 0)>0 for r in rows),"data":[{"symbol":r.low.underlying,"instrument_class":r.low.instrument_class,"expiry":r.low.expiry,"expiry_date":_expiry_date(r.low.expiry),"expiry_type":_expiry_types.get((str(r.low.underlying).strip().upper(), _expiry_key(r.low.expiry)), "UNKNOWN"),"low_strike":r.low.strike,"high_strike":r.high.strike,"direction":r.direction,"timestamp_ns":r.low.timestamp_ns,"high_timestamp_ns":r.high.timestamp_ns,"last_update_timestamp_ns":max(int(r.low.timestamp_ns),int(r.high.timestamp_ns)),"quote_age_seconds":round(max(0.0,(now_ns-max(int(r.low.timestamp_ns),int(r.high.timestamp_ns)))/1_000_000_000),3),"freshness":"LIVE","timestamp_skew_ms":round((max(int(getattr(r.low,"call_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.low,"put_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.high,"call_timestamp_ns",None) or r.high.timestamp_ns),int(getattr(r.high,"put_timestamp_ns",None) or r.high.timestamp_ns))-min(int(getattr(r.low,"call_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.low,"put_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.high,"call_timestamp_ns",None) or r.high.timestamp_ns),int(getattr(r.high,"put_timestamp_ns",None) or r.high.timestamp_ns)))/1_000_000,3),"low_call_ltp":getattr(r.low,"call_ltp",None),"low_call_bid":r.low.call_bid,"low_call_ask":r.low.call_ask,"low_put_ltp":getattr(r.low,"put_ltp",None),"low_put_bid":r.low.put_bid,"low_put_ask":r.low.put_ask,"high_call_ltp":getattr(r.high,"call_ltp",None),"high_call_bid":r.high.call_bid,"high_call_ask":r.high.call_ask,"high_put_ltp":getattr(r.high,"put_ltp",None),"high_put_bid":r.high.put_bid,"high_put_ask":r.high.put_ask,"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.low.lot_size,"strike_distance":r.strike_distance,"liquidity_qty":min(r.low.volume,r.high.volume),"low_call_price":r.low.call_ask if r.direction=="LONG" else r.low.call_bid,"low_put_price":r.low.put_ask if r.direction=="LONG" else r.low.put_bid,"high_call_price":r.high.call_bid if r.direction=="LONG" else r.high.call_ask,"high_put_price":r.high.put_bid if r.direction=="LONG" else r.high.put_ask} for r in rows]}

@router.get("/snapshots")
def snapshots(
    days: int = Query(1, ge=1, le=30),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """Paginated persisted observations; total_count always reports the full window."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with SessionLocal() as db:
        base_query = db.query(LiveBoxSpreadSnapshot).filter(
            LiveBoxSpreadSnapshot.observed_at >= cutoff
        )
        total_count = base_query.count()
        rows = (
            base_query
            .order_by(
                LiveBoxSpreadSnapshot.observed_at.desc(),
                LiveBoxSpreadSnapshot.id.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        data = []
        for row in rows:
            try:
                item = json.loads(row.payload_json)
            except (TypeError, ValueError):
                continue
            item["observed_at"] = row.observed_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            item["expiry_type"] = _expiry_types.get(
                (str(row.symbol).strip().upper(), _expiry_key(row.expiry)), "UNKNOWN"
            )
            item["freshness"] = "SAVED SNAPSHOT"
            data.append(item)
        return {
            "strategy": "box-spread",
            "days": days,
            "count": len(data),
            "total_count": total_count,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < total_count,
            "data": data,
        }


@router.get("/alerts")
def alerts(days:int=Query(30,ge=1,le=30),limit:int=Query(500,ge=1,le=5000)):
    from datetime import datetime,timedelta
    cutoff=datetime.utcnow()-timedelta(days=days)
    with SessionLocal() as db:
        rows=db.query(LiveBoxSpreadAlertHistory).filter(LiveBoxSpreadAlertHistory.observed_at>=cutoff).order_by(LiveBoxSpreadAlertHistory.observed_at.desc()).limit(limit).all()
        return {"strategy":"box-spread","days":days,"count":len(rows),"data":[{"symbol":r.symbol,"instrument_class":r.instrument_class,"expiry":r.expiry,"low_strike":r.low_strike,"high_strike":r.high_strike,"direction":r.direction,"timestamp_ns":r.timestamp_ns,"low_call_bid":r.low_call_bid,"low_call_ask":r.low_call_ask,"low_put_bid":r.low_put_bid,"low_put_ask":r.low_put_ask,"high_call_bid":r.high_call_bid,"high_call_ask":r.high_call_ask,"high_put_bid":r.high_put_bid,"high_put_ask":r.high_put_ask,"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.lot_size} for r in rows]}

__all__=["router","configure"]
