from __future__ import annotations
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.user_context import current_user_id
from app.models import AlertRule, AlertContact, User

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
    rules = db.query(AlertRule).filter(AlertRule.user_id == uid).order_by(AlertRule.priority.desc(), AlertRule.id.asc()).all()
    user = db.query(User).filter(User.id == uid).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {"status":"success","alerts":{"enabled":bool(user.alerts_enabled)}, "strategies":sorted(STRATEGIES),
            "metrics":sorted(ALERT_METRICS), "operators":sorted(ALERT_OPERATORS),
            "notification_channels":{"whatsapp": bool(__import__("app.core.config", fromlist=["settings"]).settings.WHATSAPP_ENABLED), "telegram": bool(__import__("app.core.config", fromlist=["settings"]).settings.TELEGRAM_ENABLED), "email": bool(__import__("app.core.config", fromlist=["settings"]).settings.EMAIL_ENABLED)},
            "notification_preferences":{"whatsapp": bool(user.whatsapp_alerts_enabled), "telegram": bool(user.telegram_alerts_enabled), "email": bool(user.email_alerts_enabled), "email_address": user.alert_email or ""},
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


@router.get("/history")
def get_alert_history(days: int = 30, limit: int = 500, db: Session = Depends(get_db)):
    """Return bounded scanner-global alert history from all four live scanners."""
    if not 1 <= days <= 30:
        raise HTTPException(status_code=422, detail="days must be between 1 and 30")
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    from app.models import (
        LiveCashFutureAlertHistory, LiveCalendarSpreadAlertHistory,
        LiveSyntheticAlertHistory, LiveBoxSpreadAlertHistory,
    )
    rows = []
    for strategy, model in (
        ("cash-future", LiveCashFutureAlertHistory),
        ("calendar-spread", LiveCalendarSpreadAlertHistory),
        ("synthetic-future-cash-carry", LiveSyntheticAlertHistory),
        ("box-spread", LiveBoxSpreadAlertHistory),
    ):
        for row in db.query(model).filter(model.observed_at >= cutoff).order_by(model.observed_at.desc()).limit(limit).all():
            if strategy == "cash-future":
                payload = {
                    "strategy_id": strategy, "observed_at": row.observed_at, "symbol": row.symbol,
                    "direction": None, "event": row.event, "gross_profit": row.gross_profit,
                    "net_profit": row.net_profit, "edge": row.gap,
                    "details": {"timestamp_ns": row.timestamp_ns, "contract_month": row.contract_month,
                        "cash_ask": row.cash_ask, "future_bid": row.future_bid, "gap": row.gap,
                        "gap_pct": row.gap_pct, "lot_size": row.lot_size, "alert_lots": row.alert_lots,
                        "estimated_cost": row.estimated_cost, "net_gap_pct": row.net_gap_pct,
                        "annualized_gap_pct": row.annualized_gap_pct, "liquidity_qty": row.liquidity_qty,
                        "stable_observations": row.stable_observations},
                }
            elif strategy == "calendar-spread":
                payload = {
                    "strategy_id": strategy, "observed_at": row.observed_at, "symbol": row.underlying,
                    "direction": row.direction, "event": None, "gross_profit": row.gross_profit,
                    "net_profit": None, "edge": row.gap_points,
                    "details": {"timestamp_ns": row.timestamp_ns, "exchange": row.exchange,
                        "near_contract_month": row.near_contract_month, "far_contract_month": row.far_contract_month,
                        "edge_long": row.edge_long, "edge_short": row.edge_short,
                        "gap_points": row.gap_points, "lot_size": row.lot_size},
                }
            elif strategy == "synthetic-future-cash-carry":
                payload = {
                    "strategy_id": strategy, "observed_at": row.observed_at, "symbol": row.symbol,
                    "direction": row.direction, "event": None, "gross_profit": row.gross_pnl,
                    "net_profit": None, "edge": row.executable_edge,
                    "details": {"timestamp_ns": row.timestamp_ns, "instrument_class": row.instrument_class,
                        "expiry": row.expiry, "strike": row.strike, "executable_edge": row.executable_edge,
                        "edge_per_lot": row.edge_per_lot, "gross_pnl": row.gross_pnl, "lot_size": row.lot_size},
                }
            else:
                payload = {
                    "strategy_id": strategy, "observed_at": row.observed_at, "symbol": row.symbol,
                    "direction": row.direction, "event": None, "gross_profit": row.gross_pnl,
                    "net_profit": None, "edge": row.executable_edge,
                    "details": {"timestamp_ns": row.timestamp_ns, "instrument_class": row.instrument_class,
                        "expiry": row.expiry, "low_strike": row.low_strike, "high_strike": row.high_strike,
                        "low_call_bid": row.low_call_bid, "low_call_ask": row.low_call_ask,
                        "low_put_bid": row.low_put_bid, "low_put_ask": row.low_put_ask,
                        "high_call_bid": row.high_call_bid, "high_call_ask": row.high_call_ask,
                        "high_put_bid": row.high_put_bid, "high_put_ask": row.high_put_ask,
                        "executable_edge": row.executable_edge, "edge_per_lot": row.edge_per_lot,
                        "gross_pnl": row.gross_pnl, "lot_size": row.lot_size},
                }
            rows.append(payload)
    rows.sort(key=lambda item: item["observed_at"] or datetime.min, reverse=True)
    return {"status": "success", "history_scope": "scanner_global", "days": days,
            "count": min(len(rows), limit), "data": rows[:limit]}

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
    mobile_number: str = Field(default="", max_length=32)
    email_address: str | None = Field(default=None, max_length=320)
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
    db.add(row)
    user = db.query(User).filter(User.id == uid).first()
    if email:
        user.alert_email = email
    db.commit(); db.refresh(row)
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
    user = db.query(User).filter(User.id == uid).first()
    if email:
        user.alert_email = email
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
