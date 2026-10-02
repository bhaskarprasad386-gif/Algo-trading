from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.execution.paper_routes import current_user_id
from app.models import AlertRule, GlobalPaperSetting

router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])

STRATEGIES = {"cash-future","calendar-spread","synthetic-future-cash-carry","box-spread"}

class AlertRuleRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=128)
    min_gross_profit: float = Field(default=0.0, ge=0)
    mobile_number: str = Field(min_length=7, max_length=32)
    whatsapp_enabled: bool = False
    enabled: bool = True
    max_loss: float = Field(default=10000.0, ge=0)
    max_daily_capital: float = Field(default=10000000.0, ge=0)
    max_simultaneous_positions: int = Field(default=20, ge=1, le=10000)
    cooldown_seconds: float = Field(default=60.0, ge=0, le=86400)
    priority: int = Field(default=0, ge=0, le=100)

class PaperGlobalRequest(BaseModel):
    enabled: bool = False
    paper_amount: float = Field(default=10000000.0, ge=0)
    emergency_stop: bool = False

def _rule_payload(r: AlertRule) -> dict:
    return {"id":r.id,"strategy_id":r.strategy_id,"min_gross_profit":r.min_gross_profit,
            "mobile_number":r.mobile_number,"whatsapp_enabled":r.whatsapp_enabled,"enabled":r.enabled,
            "max_loss":r.max_loss,"max_daily_capital":r.max_daily_capital,
            "max_simultaneous_positions":r.max_simultaneous_positions,
            "cooldown_seconds":r.cooldown_seconds,"priority":r.priority,
            "created_at":r.created_at,"updated_at":r.updated_at}

@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    uid = current_user_id(db)
    setting = db.query(GlobalPaperSetting).filter(GlobalPaperSetting.user_id == uid).first()
    if setting is None:
        setting = GlobalPaperSetting(user_id=uid)
        db.add(setting); db.commit(); db.refresh(setting)
    rules = db.query(AlertRule).filter(AlertRule.user_id == uid).order_by(AlertRule.priority.desc(), AlertRule.id.asc()).all()
    return {"status":"success","strategies":sorted(STRATEGIES),
            "paper":{"enabled":bool(setting.enabled),"paper_amount":float(setting.paper_amount),"emergency_stop":bool(setting.emergency_stop)},
            "rules":[_rule_payload(r) for r in rules]}

@router.post("/rules")
def save_rule(request: AlertRuleRequest, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    strategy = request.strategy_id.strip().lower()
    if strategy not in STRATEGIES:
        raise HTTPException(status_code=422, detail="unsupported strategy")
    r = AlertRule(user_id=uid, strategy_id=strategy, **request.model_dump(exclude={"strategy_id"}))
    db.add(r); db.commit(); db.refresh(r)
    return {"status":"success","rule":_rule_payload(r)}

@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, request: AlertRuleRequest, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    r = db.query(AlertRule).filter(AlertRule.id==rule_id, AlertRule.user_id==uid).first()
    if r is None: raise HTTPException(status_code=404, detail="alert rule not found")
    strategy = request.strategy_id.strip().lower()
    if strategy not in STRATEGIES: raise HTTPException(status_code=422, detail="unsupported strategy")
    for k,v in request.model_dump().items(): setattr(r, k, strategy if k=="strategy_id" else v)
    r.updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit(); db.refresh(r)
    return {"status":"success","rule":_rule_payload(r)}

@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    uid=current_user_id(db)
    r=db.query(AlertRule).filter(AlertRule.id==rule_id, AlertRule.user_id==uid).first()
    if r is None: raise HTTPException(status_code=404, detail="alert rule not found")
    db.delete(r); db.commit()
    return {"status":"success","deleted":rule_id}

@router.put("/paper")
def set_global_paper(request: PaperGlobalRequest, db: Session = Depends(get_db)):
    uid=current_user_id(db)
    setting=db.query(GlobalPaperSetting).filter(GlobalPaperSetting.user_id==uid).first()
    if setting is None:
        setting=GlobalPaperSetting(user_id=uid); db.add(setting)
    setting.enabled=bool(request.enabled)
    setting.paper_amount=float(request.paper_amount)
    setting.emergency_stop=bool(request.emergency_stop)
    setting.updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit(); db.refresh(setting)
    return {"status":"success","paper":{"enabled":bool(setting.enabled),"paper_amount":float(setting.paper_amount),"emergency_stop":bool(setting.emergency_stop),"live_orders":False}}

@router.post("/paper/kill-switch")
def paper_kill_switch(db: Session = Depends(get_db)):
    uid=current_user_id(db)
    setting=db.query(GlobalPaperSetting).filter(GlobalPaperSetting.user_id==uid).first()
    if setting is None:
        setting=GlobalPaperSetting(user_id=uid); db.add(setting)
    setting.enabled=False; setting.emergency_stop=True
    db.commit(); db.refresh(setting)
    return {"status":"success","enabled":False,"emergency_stop":True,"live_orders":False}
