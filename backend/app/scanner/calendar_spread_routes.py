from fastapi import APIRouter, Query
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.live_calendar_spread_scanner_result import LiveCalendarSpreadScannerResult
router=APIRouter(prefix="/api/v1/scanner/calendar-spread",tags=["Calendar Spread Scanner"])
scanner=None

def configure(value):
 global scanner; scanner=value
@router.get("/live")
def live(limit:int=Query(50,ge=1,le=200)):
 if scanner is None:return {"status":"disabled","data":[],"opportunity_count":0}
 signals=[{
  "underlying":x.underlying,"exchange":x.exchange,"instrument_type":x.instrument_type,
  "near_contract_month":x.near_contract_month,"far_contract_month":x.far_contract_month,
  "timestamp_ns":x.timestamp_ns,"near_bid":x.near_bid,"near_ask":x.near_ask,
  "far_bid":x.far_bid,"far_ask":x.far_ask,"lot_size":x.lot_size,
  "long_edge":x.edge_long,"short_edge":x.edge_short,
  "long_edge_pct":x.edge_pct_long,"short_edge_pct":x.edge_pct_short,
  "liquidity_qty":x.liquidity_qty,"capacity_lots":x.capacity_lots,"rank_score":x.rank_score
 } for x in scanner.snapshot(limit=limit)]
 return {"status":"success","scanner":"calendar-spread-live-1s","mode":"paper-safe","data":signals,"opportunity_count":sum(max(x["long_edge"],x["short_edge"])>0 for x in signals)}
@router.get("/history")
def history(days:int=Query(1,ge=1,le=30),limit:int=Query(200,ge=1,le=1000)):
 from datetime import datetime,timedelta,timezone
 db=SessionLocal()
 try:
  since=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days)
  rows=db.query(LiveCalendarSpreadScannerResult).filter(LiveCalendarSpreadScannerResult.observed_at>=since).order_by(LiveCalendarSpreadScannerResult.rank_score.desc()).limit(limit).all()
  return {"status":"success","data":[{c.name:getattr(r,c.name) for c in LiveCalendarSpreadScannerResult.__table__.columns if c.name!="id"} for r in rows]}
 finally: db.close()
