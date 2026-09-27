"""HTTP access to live Box Spread opportunities and durable alerts."""
from fastapi import APIRouter,Query
from app.models import LiveBoxSpreadAlertHistory
from app.core.database import SessionLocal
router=APIRouter(prefix="/api/v1/scanner/box-spread",tags=["box-spread"])
_latest=()
def configure(getter):
    global _latest; _latest=getter
@router.get("/live")
def live(limit:int=Query(50,ge=1,le=500)):
    rows=list(_latest())[:limit] if callable(_latest) else list(_latest)[:limit]
    return {"strategy":"box-spread","mode":"paper-safe","count":len(rows),"data":[{"symbol":r.low.underlying,"instrument_class":r.low.instrument_class,"expiry":r.low.expiry,"low_strike":r.low.strike,"high_strike":r.high.strike,"direction":r.direction,"timestamp_ns":r.low.timestamp_ns,"low_call_bid":r.low.call_bid,"low_call_ask":r.low.call_ask,"low_put_bid":r.low.put_bid,"low_put_ask":r.low.put_ask,"high_call_bid":r.high.call_bid,"high_call_ask":r.high.call_ask,"high_put_bid":r.high.put_bid,"high_put_ask":r.high.put_ask,"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.low.lot_size,"strike_distance":r.strike_distance} for r in rows]}
@router.get("/alerts")
def alerts(days:int=Query(30,ge=1,le=30),limit:int=Query(500,ge=1,le=5000)):
    from datetime import datetime,timedelta
    cutoff=datetime.utcnow()-timedelta(days=days)
    with SessionLocal() as db:
        rows=db.query(LiveBoxSpreadAlertHistory).filter(LiveBoxSpreadAlertHistory.observed_at>=cutoff).order_by(LiveBoxSpreadAlertHistory.observed_at.desc()).limit(limit).all()
        return {"strategy":"box-spread","days":days,"count":len(rows),"data":[{"symbol":r.symbol,"instrument_class":r.instrument_class,"expiry":r.expiry,"low_strike":r.low_strike,"high_strike":r.high_strike,"direction":r.direction,"timestamp_ns":r.timestamp_ns,"low_call_bid":r.low_call_bid,"low_call_ask":r.low_call_ask,"low_put_bid":r.low_put_bid,"low_put_ask":r.low_put_ask,"high_call_bid":r.high_call_bid,"high_call_ask":r.high_call_ask,"high_put_bid":r.high_put_bid,"high_put_ask":r.high_put_ask,"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.lot_size} for r in rows]}
__all__=["router","configure"]
