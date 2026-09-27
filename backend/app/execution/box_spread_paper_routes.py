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
 if req.liquidity_qty if hasattr(req,"liquidity_qty") else False: pass

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
 # Reserve the maximum cash debit for the four executable legs; no live broker order is sent.
 notional=(req.low_call_price+req.low_put_price+req.high_call_price+req.high_put_price)*q
 if a.virtual_balance<notional:raise HTTPException(400,detail="insufficient paper balance")
 p=LiveBoxSpreadPaperPosition(user_id=user,underlying=req.underlying.upper(),instrument_class=req.instrument_class.upper(),expiry=req.expiry,low_strike=req.low_strike,high_strike=req.high_strike,direction=req.direction,lot_size=req.lot_size,lots=req.lots,low_call_entry=req.low_call_price,low_put_entry=req.low_put_price,high_call_entry=req.high_call_price,high_put_entry=req.high_put_price)
 db.add(p);a.virtual_balance=round(a.virtual_balance-notional,8);db.commit()
 return {"status":"success","mode":"paper","position_id":p.id,"position":p.__dict__|{"_sa_instance_state":None},"virtual_balance":a.virtual_balance}

@router.post("/entry")
def entry(req:Entry,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 _validate(req); return _entry(req,user,db)

@router.post("/exit")
def exit(req:Exit,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active box spread paper position")
 a=_acct(db,user);q=p.lot_size*p.lots
 if p.direction=="LONG":
  pnl=((p.low_call_entry-req.low_call_price)+(p.low_put_entry-req.low_put_price)+(req.high_call_price-p.high_call_entry)+(req.high_put_price-p.high_put_entry))*q
 else:
  pnl=((req.low_call_price-p.low_call_entry)+(req.low_put_price-p.low_put_entry)+(p.high_call_entry-req.high_call_price)+(p.high_put_entry-req.high_put_price))*q
 entry_notional=(p.low_call_entry+p.low_put_entry+p.high_call_entry+p.high_put_entry)*q
 a.virtual_balance=round(a.virtual_balance+entry_notional+pnl,8);a.realized_pnl=round(a.realized_pnl+pnl,8);p.realized_pnl=pnl;p.is_open=0;db.commit()
 return {"status":"success","mode":"paper","position_id":p.id,"gross_pnl":round(pnl,8),"realized_pnl":a.realized_pnl,"virtual_balance":a.virtual_balance}

@router.get("/position")
def position(user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveBoxSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 return {"status":"active" if p else "flat","position":None if not p else {k:getattr(p,k) for k in ("id","underlying","instrument_class","expiry","low_strike","high_strike","direction","lot_size","lots","low_call_entry","low_put_entry","high_call_entry","high_put_entry")}}
