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
 signals=[]
 for key,bucket in scanner._latest.items():
  if len(bucket)<2:continue
  months=sorted(bucket,key=lambda m:bucket[m][5]); n=bucket[months[0]]; f=bucket[months[1]]
  if n[0]!=f[0]:continue
  base=(n[2]+f[2])/2
  if base<=0:continue
  signals.append({"underlying":key[0],"exchange":key[1],"instrument_type":n[4],"near_contract_month":months[0],"far_contract_month":months[1],"timestamp_ns":n[0],"near_bid":n[1],"near_ask":n[2],"far_bid":f[1],"far_ask":f[2],"lot_size":n[3],"long_edge":f[1]-n[2],"short_edge":n[1]-f[2],"long_edge_pct":(f[1]-n[2])/base*100,"short_edge_pct":(n[1]-f[2])/base*100,"liquidity_qty":min(n[6],n[7],f[6],f[7])})
 signals.sort(key=lambda x:max(x["long_edge_pct"],x["short_edge_pct"]),reverse=True)
 return {"status":"success","scanner":"calendar-spread-live-1s","mode":"paper-safe","data":signals[:limit],"opportunity_count":sum(max(x["long_edge"],x["short_edge"])>0 for x in signals)}
@router.get("/history")
def history(days:int=Query(1,ge=1,le=30),limit:int=Query(200,ge=1,le=1000)):
 from datetime import datetime,timedelta,timezone
 db=SessionLocal()
 try:
  since=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days)
  rows=db.query(LiveCalendarSpreadScannerResult).filter(LiveCalendarSpreadScannerResult.observed_at>=since).order_by(LiveCalendarSpreadScannerResult.rank_score.desc()).limit(limit).all()
  return {"status":"success","data":[{c.name:getattr(r,c.name) for c in LiveCalendarSpreadScannerResult.__table__.columns if c.name!="id"} for r in rows]}
 finally: db.close()
