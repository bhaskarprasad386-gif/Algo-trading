from fastapi import APIRouter, Query
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.live_calendar_spread_scanner_result import LiveCalendarSpreadScannerResult
router=APIRouter(prefix="/api/v1/scanner/calendar-spread",tags=["Calendar Spread Scanner"])
scanner=None

def configure(value):
 global scanner; scanner=value
@router.get("/live")
def live(limit:int=Query(50,ge=1,le=200), min_gap_points:float|None=Query(None,ge=0), min_gross_profit:float|None=Query(None,ge=0)):
 if scanner is None:return {"status":"disabled","data":[],"opportunity_count":0}
 effective_min_gap = scanner.minimum_gap_points if min_gap_points is None else min_gap_points
 effective_min_gross = scanner.minimum_gross_profit if min_gross_profit is None else min_gross_profit
 signals=[{
  "underlying":x.underlying,"exchange":x.exchange,"instrument_type":x.instrument_type,"contract_family":x.contract_family,
  "near_contract_month":x.near_contract_month,"far_contract_month":x.far_contract_month,
  "timestamp_ns":x.timestamp_ns,"near_timestamp_ns":x.near_timestamp_ns,"far_timestamp_ns":x.far_timestamp_ns,"timestamp_skew_ns":x.timestamp_skew_ns,"near_bid":x.near_bid,"near_ask":x.near_ask,
  "far_bid":x.far_bid,"far_ask":x.far_ask,"lot_size":x.lot_size,
  "long_edge":x.edge_long,"short_edge":x.edge_short,
  "long_edge_pct":x.edge_pct_long,"short_edge_pct":x.edge_pct_short,
  "liquidity_qty":x.liquidity_qty,"capacity_lots":x.capacity_lots,"rank_score":x.rank_score,"direction":x.direction,"gap_points":x.gap_points,"gross_profit":x.gross_profit,"profit_basis":"gross_before_fees_and_slippage","net_profit_estimate":None,"qualifies":(x.gap_points >= effective_min_gap and x.gross_profit >= effective_min_gross),"minimum_gap_points":effective_min_gap,"minimum_gross_profit":effective_min_gross
 } for x in scanner.snapshot(limit=limit, minimum_gap_points=min_gap_points, minimum_gross_profit=min_gross_profit)]
 return {"status":"success","scanner":"calendar-spread-live-1s","mode":"paper-safe","data":signals,"opportunity_count":sum(max(x["long_edge"],x["short_edge"])>0 for x in signals)}
@router.get("/pairs")
def pairs(limit:int=Query(200,ge=1,le=500)):
 if scanner is None:return {"status":"disabled","data":[],"pair_count":0}
 rows=[{
  "underlying":x.underlying,"exchange":x.exchange,"instrument_type":x.instrument_type,"contract_family":x.contract_family,
  "near_contract_month":x.near_contract_month,"far_contract_month":x.far_contract_month,
  "timestamp_ns":x.timestamp_ns,"near_timestamp_ns":x.near_timestamp_ns,"far_timestamp_ns":x.far_timestamp_ns,"timestamp_skew_ns":x.timestamp_skew_ns,"near_bid":x.near_bid,"near_ask":x.near_ask,
  "far_bid":x.far_bid,"far_ask":x.far_ask,"lot_size":x.lot_size,
  "long_edge":x.edge_long,"short_edge":x.edge_short,
  "long_edge_pct":x.edge_pct_long,"short_edge_pct":x.edge_pct_short,
  "liquidity_qty":x.liquidity_qty,"capacity_lots":x.capacity_lots,"rank_score":x.rank_score,
  "direction":x.direction,"gap_points":x.gap_points,"gross_profit":x.gross_profit,"profit_basis":"gross_before_fees_and_slippage","net_profit_estimate":None,"qualifies":x.qualifies,
 } for x in scanner.pair_snapshot(limit=limit)]
 return {"status":"success","scanner":"calendar-spread-pair-monitor","mode":"paper-safe","data":rows,"pair_count":len(rows)}
@router.get("/pair-diagnostics")
def pair_diagnostics(limit:int=Query(200,ge=1,le=500)):
 if scanner is None:return {"status":"disabled","counters":{},"data":[],"pair_count":0}
 result=scanner.diagnostics_snapshot(limit=limit)
 return {"status":"success","scanner":"calendar-spread-pair-diagnostics","mode":"paper-safe","counters":result["counters"],"data":result["pairs"],"pair_count":len(result["pairs"])}
def _ensure_contract_family_history_column(db):
 from sqlalchemy import text
 if db.get_bind().dialect.name != "sqlite":
  return
 columns={row[1] for row in db.execute(text("PRAGMA table_info(live_calendar_spread_scanner_results)")).fetchall()}
 if columns and "contract_family" not in columns:
  db.execute(text("ALTER TABLE live_calendar_spread_scanner_results ADD COLUMN contract_family VARCHAR NOT NULL DEFAULT 'UNKNOWN'"))
  db.commit()

@router.get("/history")
def history(days:int=Query(1,ge=1,le=90),limit:int=Query(200,ge=1,le=1000)):
 from datetime import datetime,timedelta,timezone
 db=SessionLocal()
 try:
  _ensure_contract_family_history_column(db)
  since=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days)
  rows=db.query(LiveCalendarSpreadScannerResult).filter(LiveCalendarSpreadScannerResult.observed_at>=since).order_by(LiveCalendarSpreadScannerResult.rank_score.desc()).limit(limit).all()
  return {"status":"success","data":[{c.name:getattr(r,c.name) for c in LiveCalendarSpreadScannerResult.__table__.columns if c.name!="id"} for r in rows]}
 finally: db.close()
