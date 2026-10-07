from __future__ import annotations
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.execution.paper_routes import current_user_id
from app.models import AlertRule, AlertContact, GlobalPaperSetting, User

router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])

STRATEGIES = {"cash-future","calendar-spread","synthetic-future-cash-carry","box-spread"}
ALERT_METRICS = {"gap","gross_profit","net_profit","volume","oi","iv","premium","spread_value"}
ALERT_OPERATORS = {">=", ">", "<=", "<", "="}
ALERT_STRATEGY_METRICS = {
    "cash-future": {"gap", "gross_profit", "net_profit"},
    "calendar-spread": {"gap", "gross_profit"},
    "synthetic-future-cash-carry": {"gap", "gross_profit"},
    "box-spread": {"gap", "gross_profit"},
}

def _validate_strategy_metric(strategy: str, metric: str) -> None:
    if metric not in ALERT_STRATEGY_METRICS.get(strategy, set()):
        raise HTTPException(status_code=422, detail=f"metric '{metric}' is not supported for strategy '{strategy}'")

class AlertRuleRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="Unnamed Alert", min_length=1, max_length=128)
    metric: str = Field(default="gross_profit", min_length=1, max_length=32)
    operator: str = Field(default=">=", min_length=1, max_length=2)
    threshold: float = Field(default=0.0)
    min_gross_profit: float = Field(default=0.0, ge=0)
    mobile_number: str = Field(default="", max_length=32)
    email_address: str | None = Field(default=None, max_length=320)
    whatsapp_enabled: bool = False
    enabled: bool = True
    max_loss: float = Field(default=10000.0, ge=0)
    max_daily_capital: float = Field(default=10000000.0, ge=0)
    max_simultaneous_positions: int = Field(default=20, ge=1, le=10000)
    cooldown_seconds: float = Field(default=60.0, ge=0, le=86400)
    priority: int = Field(default=0, ge=0, le=100)

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ALERT_METRICS:
            raise ValueError("unsupported alert metric")
        return value

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, value: str) -> str:
        if value not in ALERT_OPERATORS:
            raise ValueError("unsupported alert operator")
        return value

    @field_validator("threshold")
    @classmethod
    def validate_threshold(cls, value: float) -> float:
        import math
        if not math.isfinite(value):
            raise ValueError("threshold must be finite")
        return value

class PaperGlobalRequest(BaseModel):
    enabled: bool = False
    paper_amount: float = Field(default=10000000.0, ge=0)
    emergency_stop: bool = False

def _rule_payload(r: AlertRule) -> dict:
    return {"id":r.id,"name":r.name,"strategy_id":r.strategy_id,"metric":r.metric,"operator":r.operator,"threshold":r.threshold,"min_gross_profit":r.min_gross_profit,
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
    user = db.query(User).filter(User.id == uid).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {"status":"success","alerts":{"enabled":bool(user.alerts_enabled)}, "strategies":sorted(STRATEGIES),
            "metrics":sorted(ALERT_METRICS), "operators":sorted(ALERT_OPERATORS),
            "notification_channels":{"whatsapp": bool(__import__("app.core.config", fromlist=["settings"]).settings.WHATSAPP_ENABLED), "telegram": bool(__import__("app.core.config", fromlist=["settings"]).settings.TELEGRAM_ENABLED), "email": bool(user.email_alerts_enabled)},
            "notification_preferences":{"whatsapp": bool(user.whatsapp_alerts_enabled), "telegram": bool(user.telegram_alerts_enabled), "email": bool(user.email_alerts_enabled), "email_address": user.alert_email or ""},
            "paper":{"enabled":bool(setting.enabled),"paper_amount":float(setting.paper_amount),"emergency_stop":bool(setting.emergency_stop)},
            "rules":[_rule_payload(r) for r in rules]}

@router.get("/status")
def get_alert_status(db: Session = Depends(get_db)):
    """Return durable alert health/status without suppressing scanner results."""
    uid = current_user_id(db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=30)
    active_rules = db.query(AlertRule).filter(
        AlertRule.user_id == uid, AlertRule.enabled.is_(True)
    ).count()
    from app.models import (
        LiveCashFutureAlertHistory,
        LiveCalendarSpreadAlertHistory,
        LiveSyntheticAlertHistory,
        LiveBoxSpreadAlertHistory,
    )
    history_models = {
        "cash-future": LiveCashFutureAlertHistory,
        "calendar-spread": LiveCalendarSpreadAlertHistory,
        "synthetic-future-cash-carry": LiveSyntheticAlertHistory,
        "box-spread": LiveBoxSpreadAlertHistory,
    }
    by_strategy = {}
    for strategy, model in history_models.items():
        by_strategy[strategy] = db.query(model).filter(model.observed_at >= cutoff).count()
    triggered_30d = sum(by_strategy.values())
    return {
        "status": "success",
        "alerts": {"enabled": bool(db.query(User).filter(User.id == uid).first().alerts_enabled)},
        "active_rules": active_rules,
        "triggered_30d": triggered_30d,
        "history_30d": triggered_30d,
        "history_scope": "scanner_global",
        "by_strategy": by_strategy,
        "window_days": 30,
    }

@router.post("/rules")
def save_rule(request: AlertRuleRequest, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    strategy = request.strategy_id.strip().lower()
    if strategy not in STRATEGIES:
        raise HTTPException(status_code=422, detail="unsupported strategy")
    _validate_strategy_metric(strategy, request.metric)
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
    _validate_strategy_metric(strategy, request.metric)
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

class AlertContactRequest(BaseModel):
    label: str = Field(default="Primary", min_length=1, max_length=64)
    mobile_number: str = Field(min_length=7, max_length=32)
    enabled: bool = True
    sms_enabled: bool = False
    app_enabled: bool = True
    whatsapp_enabled: bool = False
    telegram_enabled: bool = False
    telegram_chat_id: str = Field(default="", max_length=128)
    email_enabled: bool = False

def _contact_payload(c: AlertContact) -> dict:
    return {"id": c.id, "label": c.label, "mobile_number": c.mobile_number, "email_address": c.email_address or "",
            "enabled": bool(c.enabled), "channels": {
                "sms": bool(c.sms_enabled), "app": bool(c.app_enabled),
                "whatsapp": bool(c.whatsapp_enabled), "telegram": bool(c.telegram_enabled), "email": bool(c.email_enabled)},
            "telegram_chat_id": c.telegram_chat_id,
            "created_at": c.created_at, "updated_at": c.updated_at}

@router.get("/contacts")
def list_alert_contacts(db: Session = Depends(get_db)):
    uid = current_user_id(db)
    rows = db.query(AlertContact).filter(AlertContact.user_id == uid).order_by(AlertContact.id.asc()).all()
    return {"status": "success", "contacts": [_contact_payload(row) for row in rows]}

@router.post("/contacts")
def add_alert_contact(request: AlertContactRequest, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    number = request.mobile_number.strip()
    email = (request.email_address or "").strip()
    chat_id = request.telegram_chat_id.strip()
    if request.telegram_enabled and not chat_id:
        raise HTTPException(status_code=422, detail="telegram chat id required when Telegram is enabled")
    if request.email_enabled and ("@" not in email or "." not in email.split("@")[-1]):
        raise HTTPException(status_code=422, detail="valid email address required when Email is enabled")
    if not number.isdigit() and not email:
        raise HTTPException(status_code=422, detail="mobile number or email address required")
    if number and (not number.isdigit() or len(number) < 7):
        raise HTTPException(status_code=422, detail="invalid mobile number")
    row = AlertContact(user_id=uid, mobile_number=number, **request.model_dump(exclude={"mobile_number"}))
    db.add(row); db.commit(); db.refresh(row)
    return {"status": "success", "contact": _contact_payload(row)}

@router.put("/contacts/{contact_id}")
def update_alert_contact(contact_id: int, request: AlertContactRequest, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    row = db.query(AlertContact).filter(AlertContact.id == contact_id, AlertContact.user_id == uid).first()
    if row is None:
        raise HTTPException(status_code=404, detail="alert contact not found")
    number = request.mobile_number.strip()
    email = (request.email_address or "").strip()
    chat_id = request.telegram_chat_id.strip()
    if request.telegram_enabled and not chat_id:
        raise HTTPException(status_code=422, detail="telegram chat id required when Telegram is enabled")
    if request.email_enabled and ("@" not in email or "." not in email.split("@")[-1]):
        raise HTTPException(status_code=422, detail="valid email address required when Email is enabled")
    if not number.isdigit() and not email:
        raise HTTPException(status_code=422, detail="mobile number or email address required")
    if number and (not number.isdigit() or len(number) < 7):
        raise HTTPException(status_code=422, detail="invalid mobile number")
    for key, value in request.model_dump().items():
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit(); db.refresh(row)
    return {"status": "success", "contact": _contact_payload(row)}

@router.delete("/contacts/{contact_id}")
def delete_alert_contact(contact_id: int, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    row = db.query(AlertContact).filter(AlertContact.id == contact_id, AlertContact.user_id == uid).first()
    if row is None:
        raise HTTPException(status_code=404, detail="alert contact not found")
    db.delete(row); db.commit()
    return {"status": "success", "deleted": contact_id}


class AlertChannelRequest(BaseModel):
    channel: str = Field(min_length=1, max_length=16)
    enabled: bool

@router.put("/channels")
def set_alert_channel(request: AlertChannelRequest, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    user = db.query(User).filter(User.id == uid).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    fields = {"whatsapp":"whatsapp_alerts_enabled","telegram":"telegram_alerts_enabled","email":"email_alerts_enabled"}
    field = fields.get(request.channel.strip().lower())
    if field is None:
        raise HTTPException(status_code=422, detail="unsupported notification channel")
    if request.enabled and field == "email_alerts_enabled" and not user.alert_email:
        raise HTTPException(status_code=422, detail="save an alert email before enabling Email")
    setattr(user, field, request.enabled)
    user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status":"success","channel":request.channel.strip().lower(),"enabled":bool(getattr(user, field))}

class AlertMasterRequest(BaseModel):
    enabled: bool

@router.put("/master")
def set_alert_master(request: AlertMasterRequest, db: Session = Depends(get_db)):
    uid = current_user_id(db)
    user = db.query(User).filter(User.id == uid).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    user.alerts_enabled = request.enabled
    user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status":"success","alerts":{"enabled":bool(user.alerts_enabled)}}

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
