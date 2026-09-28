from fastapi import APIRouter,Depends,HTTPException
import json
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.execution.paper_routes import current_user_id
from app.models import TradingAccount, SystemLog
from app.models.live_box_spread_paper_position import LiveBoxSpreadPaperPosition
from app.risk.engine import RiskLimits

router=APIRouter(prefix="/api/v1/execution/paper/box-spread",tags=["Box Spread Paper"])

class Entry(BaseModel):
 underlying:str=Field(min_length=1); instrument_class:str; expiry:str
 low_strike:float; high_strike:float; direction:str; lot_size:int=Field(gt=0); lots:int=Field(gt=0)
 low_call_price:float=Field(gt=0); low_put_price:float=Field(gt=0); high_call_price:float=Field(gt=0); high_put_price:float=Field(gt=0)

class Exit(BaseModel):
 low_call_price:float=Field(gt=0); low_put_price:float=Field(gt=0); high_call_price:float=Field(gt=0); high_put_price:float=Field(gt=0)

class ScannerEntry(Entry):
 executable_edge:float|None=None; edge_per_lot:float|None=None; liquidity_qty:float=Field(default=0,ge=0)

class AutoCycleSettings(BaseModel):
 lots:int=Field(default=1,ge=1)

@router.get("/auto-cycle-settings")
def auto_cycle_settings(user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 a=_acct(db,user)
 return {"status":"success","mode":"paper","lots":max(1, int(a.box_spread_auto_lots))}

@router.put("/auto-cycle-settings")
def update_auto_cycle_settings(req:AutoCycleSettings,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 a=_acct(db,user)
 if req.lots > RiskLimits().max_position_quantity: raise HTTPException(422,detail="lots exceed risk limit")
 a.box_spread_auto_lots=req.lots
 db.commit()
 return {"status":"success","mode":"paper","lots":a.box_spread_auto_lots}


@router.get("/journal")
def journal(limit:int=50,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 if limit < 1 or limit > 200: raise HTTPException(422,detail="limit must be between 1 and 200")
 rows=(db.query(SystemLog)
       .filter(SystemLog.module=="box_spread_paper_cycle")
       .filter(SystemLog.message.like(f"user={user} %"))
       .order_by(SystemLog.created_at.desc())
       .limit(limit).all())
 items=[]
 for r in rows:
  try: details=json.loads(r.details or "{}")
  except (TypeError,ValueError): details={"raw":r.details}
  items.append({"id":r.id,"created_at":r.created_at,**details})
 return {"status":"success","mode":"paper","count":len(items),"items":items}


def _audit(db,user,event,**fields):
    payload={"event":event,"user_id":user,**fields}
    db.add(SystemLog(level="INFO",module="box_spread_paper_cycle",message=f"user={user} event={event}",details=json.dumps(payload,sort_keys=True,default=str)))

def _acct(db,user):
 a=db.query(TradingAccount).filter_by(user_id=user).first()
 if not a or not a.is_active or a.mode.upper()!="PAPER":raise HTTPException(409,detail="paper account unavailable")
 return a

def _validate(req):
 if req.direction not in {"LONG","SHORT"}:raise HTTPException(422,detail="invalid direction")
 if req.instrument_class.upper() not in {"STOCK","INDEX"}:raise HTTPException(422,detail="invalid instrument class")
 if req.high_strike<=req.low_strike:raise HTTPException(422,detail="high strike must exceed low strike")
 if req.lots > RiskLimits().max_position_quantity // req.lot_size:raise HTTPException(422,detail="position quantity exceeds risk limit")
 from datetime import date,datetime
 try: expiry=date.fromisoformat(req.expiry[:4]+"-"+req.expiry[4:6]+"-"+req.expiry[6:8])
 except (ValueError,IndexError): raise HTTPException(422,detail="invalid expiry")
 if expiry <= datetime.now().date():raise HTTPException(422,detail="entry not allowed on or after expiry")

def _entry_cashflow(req:Entry)->float:
    if req.direction=="LONG":
        return req.high_call_price+req.high_put_price-req.low_call_price-req.low_put_price
    return req.low_call_price+req.low_put_price-req.high_call_price-req.high_put_price

def _exit_cashflow(p,req:Exit)->float:
    if p.direction=="LONG":
        return req.low_call_price+req.low_put_price-req.high_call_price-req.high_put_price
    return req.high_call_price+req.high_put_price-req.low_call_price-req.low_put_price

def _position_entry_cashflow(p):
    if p.direction=="LONG":
        return p.high_call_entry+p.high_put_entry-p.low_call_entry-p.low_put_entry
    return p.low_call_entry+p.low_put_entry-p.high_call_entry-p.high_put_entry

def _scanner_entry_request(match, lots=1):
    return ScannerEntry(underlying=match.low.underlying,instrument_class=match.low.instrument_class,expiry=str(match.low.expiry),low_strike=match.low.strike,high_strike=match.high.strike,direction=match.direction,lot_size=match.low.lot_size,lots=lots,low_call_price=match.low.call_ask if match.direction=="LONG" else match.low.call_bid,low_put_price=match.low.put_ask if match.direction=="LONG" else match.low.put_bid,high_call_price=match.high.call_bid if match.direction=="LONG" else match.high.call_ask,high_put_price=match.high.put_bid if match.direction=="LONG" else match.high.put_ask,executable_edge=match.executable_edge,edge_per_lot=match.edge_per_lot,liquidity_qty=min(match.low.volume,match.high.volume))

@router.post("/auto-entry")
def auto_entry(lots:int=1,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 if lots<=0: raise HTTPException(422,detail="lots must be positive")
 if lots > RiskLimits().max_position_quantity: raise HTTPException(422,detail="lots exceed risk limit")
 from app.scanner import live_box_spread_routes as scanner
 rows=list(scanner._latest()) if callable(scanner._latest) else list(scanner._latest)
 eligible=[r for r in rows if r.executable_edge>0 and min(r.low.volume,r.high.volume)>=r.low.lot_size*lots]
 if not eligible: raise HTTPException(409,detail="no executable box spread opportunity available")
 match=max(eligible,key=lambda r:r.executable_edge)
 req=_scanner_entry_request(match,lots)
 _validate(req)
 if db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first(): raise HTTPException(409,detail="box spread paper position already open")
 return _entry(req,user,db)

@router.post("/from-scanner")
def from_scanner(req:ScannerEntry,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 _validate(req)
 if req.executable_edge is None or req.executable_edge<=0:raise HTTPException(409,detail="scanner edge is not positive")
 if req.liquidity_qty < req.lot_size*req.lots:raise HTTPException(409,detail="scanner liquidity is below requested quantity")
 return _entry(req,user,db)

def _entry(req,user,db):
 _validate(req)
 a=_acct(db,user)
 if db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first():raise HTTPException(409,detail="box spread paper position already open")
 q=req.lot_size*req.lots
 cashflow=_entry_cashflow(req)*q
 if cashflow<0 and a.virtual_balance < -cashflow:raise HTTPException(400,detail="insufficient paper balance")
 p=LiveBoxSpreadPaperPosition(user_id=user,underlying=req.underlying.upper(),instrument_class=req.instrument_class.upper(),expiry=req.expiry,low_strike=req.low_strike,high_strike=req.high_strike,direction=req.direction,lot_size=req.lot_size,lots=req.lots,low_call_entry=req.low_call_price,low_put_entry=req.low_put_price,high_call_entry=req.high_call_price,high_put_entry=req.high_put_price)
 db.add(p);a.virtual_balance=round(a.virtual_balance+cashflow,8);db.flush();_audit(db,user,"ENTRY",position_id=p.id,underlying=p.underlying,instrument_class=p.instrument_class,expiry=p.expiry,direction=p.direction,low_strike=p.low_strike,high_strike=p.high_strike,lots=p.lots,quantity=q,low_call_price=req.low_call_price,low_put_price=req.low_put_price,high_call_price=req.high_call_price,high_put_price=req.high_put_price,entry_cashflow=cashflow);db.commit()
 return {"status":"success","mode":"paper","position_id":p.id,"position":p.__dict__|{"_sa_instance_state":None},"virtual_balance":a.virtual_balance}

@router.post("/entry")
def entry(req:Entry,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 _validate(req); return _entry(req,user,db)

def _current_scanner_match(p):
 from app.scanner import live_box_spread_routes as scanner
 rows=list(scanner._latest()) if callable(scanner._latest) else list(scanner._latest)
 return next((r for r in rows if r.low.underlying.upper()==p.underlying and r.low.instrument_class.upper()==p.instrument_class and str(r.low.expiry)==str(p.expiry) and float(r.low.strike)==float(p.low_strike) and float(r.high.strike)==float(p.high_strike)),None)

def _scanner_exit_request(p,match):
 return Exit(low_call_price=match.low.call_bid if p.direction=="LONG" else match.low.call_ask,low_put_price=match.low.put_bid if p.direction=="LONG" else match.low.put_ask,high_call_price=match.high.call_ask if p.direction=="LONG" else match.high.call_bid,high_put_price=match.high.put_ask if p.direction=="LONG" else match.high.put_bid)

@router.post("/exit-from-scanner")
def exit_from_scanner(user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 match=_current_scanner_match(p)
 if match is None:raise HTTPException(409,detail="live scanner quote unavailable for open box position")
 return exit(_scanner_exit_request(p,match),user,db)

@router.post("/cycle")
def cycle(lots:int=1,min_pnl:float=0.0,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 if lots<=0:raise HTTPException(422,detail="lots must be positive")
 if min_pnl<0:raise HTTPException(422,detail="min_pnl must be non-negative")
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if p:
  match=_current_scanner_match(p)
  if match is None:
   _audit(db,user,"HOLD",position_id=p.id,reason="live scanner quote unavailable",lots=p.lots)
   db.commit()
   return {"status":"hold","mode":"paper","reason":"live scanner quote unavailable","position_id":p.id}
  req=_scanner_exit_request(p,match);q=p.lot_size*p.lots
  entry_cashflow=_position_entry_cashflow(p)
  pnl=(entry_cashflow+_exit_cashflow(p,req))*q
  if pnl<min_pnl:
   _audit(db,user,"HOLD",position_id=p.id,gross_pnl=round(pnl,8),min_pnl=min_pnl,lots=p.lots)
   db.commit()
   return {"status":"hold","mode":"paper","position_id":p.id,"gross_pnl":round(pnl,8),"min_pnl":min_pnl}
  return exit(req,user,db)
 return auto_entry(lots=lots,user=user,db=db)

@router.post("/auto-exit")
def auto_exit(min_pnl:float=0.0,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 if min_pnl<0:raise HTTPException(422,detail="min_pnl must be non-negative")
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 match=_current_scanner_match(p)
 if match is None:raise HTTPException(409,detail="live scanner quote unavailable for open box position")
 req=_scanner_exit_request(p,match);q=p.lot_size*p.lots
 entry_cashflow=_position_entry_cashflow(p)
 pnl=(entry_cashflow+_exit_cashflow(p,req))*q
 if pnl<min_pnl:return {"status":"hold","mode":"paper","position_id":p.id,"gross_pnl":round(pnl,8),"min_pnl":min_pnl}
 return exit(req,user,db)

@router.post("/exit")
def exit(req:Exit,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 a=_acct(db,user);q=p.lot_size*p.lots
 entry_cashflow=_position_entry_cashflow(p)
 close_cashflow=_exit_cashflow(p,req)
 pnl=(entry_cashflow+close_cashflow)*q
 a.virtual_balance=round(a.virtual_balance+close_cashflow*q,8);a.realized_pnl=round(a.realized_pnl+pnl,8);p.realized_pnl=pnl;p.is_open=0
 from datetime import datetime,timezone
 p.closed_at=datetime.now(timezone.utc).replace(tzinfo=None)
 _audit(db,user,"EXIT",position_id=p.id,underlying=p.underlying,instrument_class=p.instrument_class,expiry=p.expiry,direction=p.direction,low_strike=p.low_strike,high_strike=p.high_strike,lots=p.lots,quantity=q,low_call_price=req.low_call_price,low_put_price=req.low_put_price,high_call_price=req.high_call_price,high_put_price=req.high_put_price,gross_pnl=round(pnl,8),realized_pnl=a.realized_pnl)
 db.commit()
 return {"status":"success","mode":"paper","position_id":p.id,"gross_pnl":round(pnl,8),"realized_pnl":a.realized_pnl,"virtual_balance":a.virtual_balance}

@router.post("/mark")
def mark(req:Exit,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 q=p.lot_size*p.lots
 entry_cashflow=_position_entry_cashflow(p)
 mark_cashflow=_exit_cashflow(p,req)
 unrealized_pnl=round((entry_cashflow+mark_cashflow)*q,8)
 return {"status":"active","mode":"paper","position_id":p.id,"direction":p.direction,"quantity":q,"unrealized_pnl":unrealized_pnl,"exit_executable_cashflow_per_unit":mark_cashflow}


@router.get("/history")
def history(limit:int=50,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 if limit < 1 or limit > 200: raise HTTPException(422,detail="limit must be between 1 and 200")
 rows=(db.query(LiveBoxSpreadPaperPosition)
       .filter(LiveBoxSpreadPaperPosition.user_id==user)
       .order_by(LiveBoxSpreadPaperPosition.created_at.desc())
       .limit(limit).all())
 return {"status":"success","mode":"paper","count":len(rows),"items":[
  {"id":p.id,"underlying":p.underlying,"instrument_class":p.instrument_class,"expiry":p.expiry,
   "low_strike":p.low_strike,"high_strike":p.high_strike,"direction":p.direction,
   "lot_size":p.lot_size,"lots":p.lots,"quantity":p.lot_size*p.lots,
   "entry_prices":{"low_call":p.low_call_entry,"low_put":p.low_put_entry,"high_call":p.high_call_entry,"high_put":p.high_put_entry},
   "realized_pnl":p.realized_pnl or 0.0,"is_open":bool(p.is_open),
   "created_at":p.created_at,"closed_at":p.closed_at} for p in rows]}

@router.get("/position")
def position(user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 return {"status":"active" if p else "flat","position":None if not p else {k:getattr(p,k) for k in ("id","underlying","instrument_class","expiry","low_strike","high_strike","direction","lot_size","lots","low_call_entry","low_put_entry","high_call_entry","high_put_entry")}}


@router.get("/overview")
def overview(limit:int=50,history_limit:int=20,journal_limit:int=20,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
    if limit<1 or limit>100: raise HTTPException(422,detail="limit must be between 1 and 100")
    if history_limit<1 or history_limit>100: raise HTTPException(422,detail="history_limit must be between 1 and 100")
    if journal_limit<1 or journal_limit>100: raise HTTPException(422,detail="journal_limit must be between 1 and 100")
    a=_acct(db,user)
    p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
    position=None if not p else {"id":p.id,"underlying":p.underlying,"instrument_class":p.instrument_class,"expiry":p.expiry,"low_strike":p.low_strike,"high_strike":p.high_strike,"direction":p.direction,"lot_size":p.lot_size,"lots":p.lots,"quantity":p.lot_size*p.lots,"entry_prices":{"low_call":p.low_call_entry,"low_put":p.low_put_entry,"high_call":p.high_call_entry,"high_put":p.high_put_entry},"realized_pnl":p.realized_pnl or 0.0,"created_at":p.created_at}
    hs=(db.query(LiveBoxSpreadPaperPosition).filter(LiveBoxSpreadPaperPosition.user_id==user).order_by(LiveBoxSpreadPaperPosition.created_at.desc()).limit(history_limit).all())
    history=[{"id":x.id,"underlying":x.underlying,"instrument_class":x.instrument_class,"expiry":x.expiry,"low_strike":x.low_strike,"high_strike":x.high_strike,"direction":x.direction,"lot_size":x.lot_size,"lots":x.lots,"quantity":x.lot_size*x.lots,"realized_pnl":x.realized_pnl or 0.0,"is_open":bool(x.is_open),"created_at":x.created_at,"closed_at":x.closed_at} for x in hs]
    js=(db.query(SystemLog).filter(SystemLog.module=="box_spread_paper_cycle").filter(SystemLog.message.like(f"user={user} %")).order_by(SystemLog.created_at.desc()).limit(journal_limit).all())
    journal=[]
    for x in js:
        try: details=json.loads(x.details or "{}")
        except (TypeError,ValueError): details={"raw":x.details}
        journal.append({"id":x.id,"created_at":x.created_at,**details})
    from app.scanner import live_box_spread_routes as scanner
    rows=list(scanner._latest()) if callable(scanner._latest) else list(scanner._latest)
    opportunities=[{"symbol":x.low.underlying,"instrument_class":x.low.instrument_class,"expiry":x.low.expiry,"low_strike":x.low.strike,"high_strike":x.high.strike,"direction":x.direction,"timestamp_ns":x.low.timestamp_ns,"low_call_bid":x.low.call_bid,"low_call_ask":x.low.call_ask,"low_put_bid":x.low.put_bid,"low_put_ask":x.low.put_ask,"high_call_bid":x.high.call_bid,"high_call_ask":x.high.call_ask,"high_put_bid":x.high.put_bid,"high_put_ask":x.high.put_ask,"executable_edge":x.executable_edge,"edge_per_lot":x.edge_per_lot,"gross_pnl":x.gross_pnl,"lot_size":x.low.lot_size,"strike_distance":x.strike_distance,"liquidity_qty":min(x.low.volume,x.high.volume),"low_call_price":x.low.call_ask if x.direction=="LONG" else x.low.call_bid,"low_put_price":x.low.put_ask if x.direction=="LONG" else x.low.put_bid,"high_call_price":x.high.call_bid if x.direction=="LONG" else x.high.call_ask,"high_put_price":x.high.put_bid if x.direction=="LONG" else x.high.put_ask} for x in rows[:limit]]
    return {"status":"success","strategy":"box-spread","mode":"paper","account":{"virtual_balance":a.virtual_balance,"realized_pnl":a.realized_pnl,"auto_cycle_lots":max(1,int(a.box_spread_auto_lots))},"open_position":position,"live_opportunities":{"count":len(opportunities),"data":opportunities},"history":{"count":len(history),"items":history},"journal":{"count":len(journal),"items":journal}}
