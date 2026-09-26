from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.execution.paper_routes import current_user_id
from app.models import TradingAccount
from app.models.live_calendar_spread_paper_position import LiveCalendarSpreadPaperPosition
router=APIRouter(prefix="/api/v1/execution/paper/calendar-spread",tags=["Calendar Spread Paper"])
class Entry(BaseModel):
 underlying:str=Field(min_length=1); exchange:str=Field(min_length=1); direction:str; near_contract_month:str; far_contract_month:str; lot_size:int=Field(gt=0); lots:int=Field(gt=0); near_price:float=Field(gt=0); far_price:float=Field(gt=0)
class Exit(BaseModel): near_price:float=Field(gt=0); far_price:float=Field(gt=0)
class ScannerEntry(Entry):
    long_edge:float|None=None; short_edge:float|None=None; liquidity_qty:float=Field(default=0,ge=0)

@router.post("/from-scanner")
def from_scanner(req:ScannerEntry,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
    edge=req.long_edge if req.direction=="LONG_NEAR_SHORT_FAR" else req.short_edge
    if edge is None or edge<=0: raise HTTPException(409,detail="scanner edge is not positive")
    if req.liquidity_qty < req.lot_size*req.lots: raise HTTPException(409,detail="scanner liquidity is below requested quantity")
    return entry(req,user,db)
def _acct(db,user):
 a=db.query(TradingAccount).filter(TradingAccount.user_id==user).first()
 if not a or not a.is_active or a.mode.upper()!="PAPER":raise HTTPException(409,detail="paper account unavailable")
 return a
@router.post("/entry")
def entry(req:Entry,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 if req.direction not in {"LONG_NEAR_SHORT_FAR","SHORT_NEAR_LONG_FAR"}:raise HTTPException(422,detail="invalid direction")
 if req.far_contract_month<=req.near_contract_month:raise HTTPException(422,detail="far contract must be later")
 if db.query(LiveCalendarSpreadPaperPosition).filter_by(user_id=user,is_open=1).first():raise HTTPException(409,detail="calendar spread paper position already open")
 a=_acct(db,user); notional=(req.near_price+req.far_price)*req.lot_size*req.lots
 if a.virtual_balance<notional:raise HTTPException(400,detail="insufficient paper balance")
 p=LiveCalendarSpreadPaperPosition(user_id=user,underlying=req.underlying.upper(),exchange=req.exchange.upper(),direction=req.direction,near_contract_month=req.near_contract_month,far_contract_month=req.far_contract_month,lot_size=req.lot_size,lots=req.lots,near_entry=req.near_price,far_entry=req.far_price);db.add(p);a.virtual_balance=round(a.virtual_balance-notional,8);db.commit();return {"status":"success","mode":"paper","position_id":p.id,"position":p.__dict__|{"_sa_instance_state":None},"virtual_balance":a.virtual_balance}
@router.post("/exit")
def exit(req:Exit,user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveCalendarSpreadPaperPosition).filter_by(user_id=user,is_open=1).first()
 if not p:raise HTTPException(404,detail="no active calendar spread paper position")
 a=_acct(db,user); q=p.lot_size*p.lots
 if p.direction=="LONG_NEAR_SHORT_FAR": pnl=((req.near_price-p.near_entry)+(p.far_entry-req.far_price))*q
 else: pnl=((p.near_entry-req.near_price)+(req.far_price-p.far_entry))*q
 a.virtual_balance=round(a.virtual_balance+(p.near_entry+p.far_entry)*q+pnl,8);a.realized_pnl=round(a.realized_pnl+pnl,8);p.realized_pnl=pnl;p.is_open=0;db.commit();return {"status":"success","mode":"paper","position_id":p.id,"gross_pnl":round(pnl,8),"realized_pnl":a.realized_pnl,"virtual_balance":a.virtual_balance}
@router.get("/position")
def position(user:int=Depends(current_user_id),db:Session=Depends(get_db)):
 p=db.query(LiveCalendarSpreadPaperPosition).filter_by(user_id=user,is_open=1).first();return {"status":"active" if p else "flat","position":None if not p else {k:getattr(p,k) for k in ("id","underlying","exchange","direction","near_contract_month","far_contract_month","lot_size","lots","near_entry","far_entry")}}
