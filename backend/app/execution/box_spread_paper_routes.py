from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.execution.paper_routes import current_user_id
from app.models import TradingAccount
from app.models.live_box_spread_paper_position import LiveBoxSpreadPaperPosition

router=APIRouter(prefix="/api/v1/execution/paper/box-spread",tags=["Box Spread Paper"])

class Entry(BaseModel):
 underlying:str=Field(min_length=1); instrument_class:str; expiry:str
 low_strike:float; high_strike:float; direction:str; lot_size:int=Field(gt=0); lots:int=Field(gt=0)
 low_call_price:float=Field(gt=0); low_put_price:float=Field(gt=0); high_call_price:float=Field(gt=0); high_put_price:float=Field(gt=0)

class Exit(BaseModel):
 low_call_price:float=Field(gt=0); low_put_price:float=Field(gt=0); high_call_price:float=Field(gt=0); high_put_price:float=Field(gt=0)

class ScannerEntry(Entry):
 executable_edge:float|None=None; edge_per_lot:float|None=None; liquidity_qty:float=Field(default=0,ge=0)

def _acct(db,user):
 a=db.query(TradingAccount).filter(TradingAccount.user_id==user).first()
 if not a or not a.is_active or a.mode.upper()!="PAPER":raise HTTPException(409,detail="paper account unavailable")
 return a

def _validate(req):
 if req.direction not in {"LONG","SHORT"}:raise HTTPException(422,detail="invalid direction")
 if req.instrument_class.upper() not in {"STOCK","INDEX"}:raise HTTPException(422,detail="invalid instrument class")
 if req.high_strike<=req.low_strike:raise HTTPException(422,detail="high strike must exceed low strike")

def _entry_cashflow(req:Entry)->float:
 low=req.low_call_price+req.low_put_price
 high=req.high_call_price+req.high_put_price
 return (high-low) if req.direction=="LONG" else (low-high)

def _exit_cashflow(p,req:Exit)->float:
 low=req.low_call_price+req.low_put_price
 high=req.high_call_price+req.high_put_price
 return (low-high) if p.direction=="LONG" else (high-low)

def _scanner_entry_request(match, lots=1):
    return ScannerEntry(underlying=match.low.underlying,instrument_class=match.low.instrument_class,expiry=str(match.low.expiry),low_strike=match.low.strike,high_strike=match.high.strike,direction=match.direction,lot_size=match.low.lot_size,lots=lots,low_call_price=match.low.call_ask if match.direction=="LONG" else match.low.call_bid,low_put_price=match.low.put_ask if match.direction=="LONG" else match.low.put_bid,high_call_price=match.high.call_bid if match.direction=="LONG" else match.high.call_ask,high_put_price=match.high.put_bid if match.direction=="LONG" else match.high.put_ask,executable_edge=match.executable_edge,edge_per_lot=match.edge_per_lot,liquidity_qty=min(match.low.volume,match.high.volume))

@router.post("/auto-entry")
def auto_entry(lots:int=1,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 if lots<=0: raise HTTPException(422,detail="lots must be positive")
 if db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first(): raise HTTPException(409,detail="box spread paper position already open")
 from app.scanner import live_box_spread_routes as scanner
 rows=list(scanner._latest()) if callable(scanner._latest) else list(scanner._latest)
 eligible=[r for r in rows if r.executable_edge>0 and min(r.low.volume,r.high.volume)>=r.low.lot_size*lots]
 if not eligible: raise HTTPException(409,detail="no executable box spread opportunity available")
 match=max(eligible,key=lambda r:r.executable_edge)
 return _entry(_scanner_entry_request(match,lots),user,db)

@router.post("/from-scanner")
def from_scanner(req:ScannerEntry,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 _validate(req)
 if req.executable_edge is None or req.executable_edge<=0:raise HTTPException(409,detail="scanner edge is not positive")
 if req.liquidity_qty < req.lot_size*req.lots:raise HTTPException(409,detail="scanner liquidity is below requested quantity")
 return _entry(req,user,db)

def _entry(req,user,db):
 a=_acct(db,user)
 if db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first():raise HTTPException(409,detail="box spread paper position already open")
 q=req.lot_size*req.lots
 cashflow=_entry_cashflow(req)*q
 if cashflow<0 and a.virtual_balance < -cashflow:raise HTTPException(400,detail="insufficient paper balance")
 p=LiveBoxSpreadPaperPosition(user_id=user,underlying=req.underlying.upper(),instrument_class=req.instrument_class.upper(),expiry=req.expiry,low_strike=req.low_strike,high_strike=req.high_strike,direction=req.direction,lot_size=req.lot_size,lots=req.lots,low_call_entry=req.low_call_price,low_put_entry=req.low_put_price,high_call_entry=req.high_call_price,high_put_entry=req.high_put_price)
 db.add(p);a.virtual_balance=round(a.virtual_balance+cashflow,8);db.commit()
 return {"status":"success","mode":"paper","position_id":p.id,"position":p.__dict__|{"_sa_instance_state":None},"virtual_balance":a.virtual_balance}

@router.post("/entry")
def entry(req:Entry,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 _validate(req); return _entry(req,user,db)

@router.post("/exit-from-scanner")
def exit_from_scanner(user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 from app.scanner import live_box_spread_routes as scanner
 rows=list(scanner._latest()) if callable(scanner._latest) else list(scanner._latest)
 match=next((r for r in rows if r.low.underlying.upper()==p.underlying and r.low.instrument_class.upper()==p.instrument_class and str(r.low.expiry)==str(p.expiry) and float(r.low.strike)==float(p.low_strike) and float(r.high.strike)==float(p.high_strike)),None)
 if match is None:raise HTTPException(409,detail="live scanner quote unavailable for open box position")
 req=Exit(low_call_price=match.low_call_bid if p.direction=="LONG" else match.low_call_ask,low_put_price=match.low_put_bid if p.direction=="LONG" else match.low_put_ask,high_call_price=match.high_call_ask if p.direction=="LONG" else match.high_call_bid,high_put_price=match.high_put_ask if p.direction=="LONG" else match.high_put_bid)
 return exit(req,user,db)

@router.post("/exit")
def exit(req:Exit,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 a=_acct(db,user);q=p.lot_size*p.lots
 entry_cashflow=(p.high_call_entry+p.high_put_entry-p.low_call_entry-p.low_put_entry) if p.direction=="LONG" else (p.low_call_entry+p.low_put_entry-p.high_call_entry-p.high_put_entry)
 close_cashflow=_exit_cashflow(p,req)
 pnl=(entry_cashflow+close_cashflow)*q
 a.virtual_balance=round(a.virtual_balance+close_cashflow*q,8);a.realized_pnl=round(a.realized_pnl+pnl,8);p.realized_pnl=pnl;p.is_open=0;db.commit()
 return {"status":"success","mode":"paper","position_id":p.id,"gross_pnl":round(pnl,8),"realized_pnl":a.realized_pnl,"virtual_balance":a.virtual_balance}

@router.post("/mark")
def mark(req:Exit,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 q=p.lot_size*p.lots
 entry_cashflow=(p.high_call_entry+p.high_put_entry-p.low_call_entry-p.low_put_entry) if p.direction=="LONG" else (p.low_call_entry+p.low_put_entry-p.high_call_entry-p.high_put_entry)
 mark_cashflow=_exit_cashflow(p,req)
 unrealized_pnl=round((entry_cashflow+mark_cashflow)*q,8)
 return {"status":"active","mode":"paper","position_id":p.id,"direction":p.direction,"quantity":q,"unrealized_pnl":unrealized_pnl,"exit_executable_cashflow_per_unit":mark_cashflow}

@router.get("/position")
def position(user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 return {"status":"active" if p else "flat","position":None if not p else {k:getattr(p,k) for k in ("id","underlying","instrument_class","expiry","low_strike","high_strike","direction","lot_size","lots","low_call_entry","low_put_entry","high_call_entry","high_put_entry")}}
