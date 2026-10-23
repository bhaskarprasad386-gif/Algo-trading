"""HTTP access to live Box Spread opportunities and durable alerts."""
from typing import Annotated
from fastapi import APIRouter, Query
import time
from app.models import LiveBoxSpreadAlertHistory
from app.core.database import SessionLocal

router=APIRouter(prefix="/api/v1/scanner/box-spread",tags=["box-spread"])
_latest=()
_expiry_types = {}
MAX_LEG_TIMESTAMP_SKEW_NS = 3_000_000_000

def _expiry_date(value):
    from datetime import datetime
    text = str(value or "").strip().upper()
    for fmt in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def configure(getter):
    global _latest; _latest=getter

def set_expiry_types(expiry_types):
    """Set expiry classifications derived from the current option instrument master."""
    global _expiry_types
    _expiry_types = dict(expiry_types or {})

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
        # Both option legs must be fresh, non-future dated, and close enough
        # in time to represent one coherent executable snapshot.
        if not (cutoff <= low_ts <= now_ns and cutoff <= high_ts <= now_ns):
            continue
        if abs(low_ts-high_ts)>max_skew_ns:
            continue
        fresh_rows.append(row)
    rows=fresh_rows[:limit]
    return {"strategy":"box-spread","mode":"paper-safe","count":len(rows),"opportunity_count":sum(float(r.executable_edge or 0)>0 for r in rows),"data":[{"symbol":r.low.underlying,"instrument_class":r.low.instrument_class,"expiry":r.low.expiry,"expiry_date":_expiry_date(r.low.expiry),"expiry_type":_expiry_types.get((str(r.low.underlying).strip().upper(), str(r.low.expiry).strip().upper()), "UNKNOWN"),"low_strike":r.low.strike,"high_strike":r.high.strike,"direction":r.direction,"timestamp_ns":r.low.timestamp_ns,"high_timestamp_ns":r.high.timestamp_ns,"last_update_timestamp_ns":max(int(r.low.timestamp_ns),int(r.high.timestamp_ns)),"quote_age_seconds":round(max(0.0,(now_ns-max(int(r.low.timestamp_ns),int(r.high.timestamp_ns)))/1_000_000_000),3),"freshness":"LIVE","timestamp_skew_ms":round(abs(int(r.low.timestamp_ns)-int(r.high.timestamp_ns))/1_000_000,3),"low_call_bid":r.low.call_bid,"low_call_ask":r.low.call_ask,"low_put_bid":r.low.put_bid,"low_put_ask":r.low.put_ask,"high_call_bid":r.high.call_bid,"high_call_ask":r.high.call_ask,"high_put_bid":r.high.put_bid,"high_put_ask":r.high.put_ask,"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.low.lot_size,"strike_distance":r.strike_distance,"liquidity_qty":min(r.low.volume,r.high.volume),"low_call_price":r.low.call_ask if r.direction=="LONG" else r.low.call_bid,"low_put_price":r.low.put_ask if r.direction=="LONG" else r.low.put_bid,"high_call_price":r.high.call_bid if r.direction=="LONG" else r.high.call_ask,"high_put_price":r.high.put_bid if r.direction=="LONG" else r.high.put_ask} for r in rows]}

@router.get("/alerts")
def alerts(days:int=Query(30,ge=1,le=30),limit:int=Query(500,ge=1,le=5000)):
    from datetime import datetime,timedelta
    cutoff=datetime.utcnow()-timedelta(days=days)
    with SessionLocal() as db:
        rows=db.query(LiveBoxSpreadAlertHistory).filter(LiveBoxSpreadAlertHistory.observed_at>=cutoff).order_by(LiveBoxSpreadAlertHistory.observed_at.desc()).limit(limit).all()
        return {"strategy":"box-spread","days":days,"count":len(rows),"data":[{"symbol":r.symbol,"instrument_class":r.instrument_class,"expiry":r.expiry,"low_strike":r.low_strike,"high_strike":r.high_strike,"direction":r.direction,"timestamp_ns":r.timestamp_ns,"low_call_bid":r.low_call_bid,"low_call_ask":r.low_call_ask,"low_put_bid":r.low_put_bid,"low_put_ask":r.low_put_ask,"high_call_bid":r.high_call_bid,"high_call_ask":r.high_call_ask,"high_put_bid":r.high_put_bid,"high_put_ask":r.high_put_ask,"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.lot_size} for r in rows]}

__all__=["router","configure"]
