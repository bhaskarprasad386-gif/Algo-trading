"""Global alert-driven live paper trading lifecycle; real broker orders stay OFF."""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from app.models.strategy_auto_paper_position import StrategyAutoPaperPosition
from app.models.global_paper_setting import GlobalPaperSetting

STRATEGIES = {"cash-future", "calendar-spread", "synthetic-future-cash-carry", "box-spread"}

@dataclass(frozen=True)
class AutoSignal:
    strategy_id: str
    symbol: str
    entry_price: float
    current_price: float
    quantity: int = 1
    expiry: str | None = None
    metadata: dict | None = None

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _parse_date(value):
    if not value: return None
    text=str(value).strip().upper()
    for fmt in ("%Y-%m-%d","%d%b%Y","%d%b%y","%d-%m-%Y","%Y%m%d"):
        try: return datetime.strptime(text,fmt).date()
        except ValueError: pass
    return None

class GlobalPaperAutoService:
    def is_enabled(self, db: Session, strategy_id: str = "") -> bool:
        setting = db.query(GlobalPaperSetting).first()
        return bool(setting and setting.enabled and not setting.emergency_stop and float(setting.paper_amount or 0) > 0)

    def set_enabled(self, db: Session, strategy_id: str, enabled: bool) -> GlobalPaperSetting:
        setting=db.query(GlobalPaperSetting).first()
        if setting is None:
            setting=GlobalPaperSetting(user_id=1,enabled=bool(enabled)); db.add(setting)
        else:
            setting.enabled=bool(enabled); setting.emergency_stop=False; setting.updated_at=_now()
        db.commit(); db.refresh(setting); return setting

    def qualify_and_enter(self, db: Session, signal: AutoSignal) -> StrategyAutoPaperPosition | None:
        if not self.is_enabled(db, signal.strategy_id): return None
        if signal.quantity<=0 or signal.entry_price<=0 or signal.current_price<=0: raise ValueError("paper-auto signal prices and quantity must be positive")
        key=signal.strategy_id.strip().lower(); setting=db.query(GlobalPaperSetting).first()
        if setting is None or float(setting.paper_amount or 0)<=0: return None
        max_quantity=max(0,int(float(setting.paper_amount)//float(signal.entry_price)))
        quantity=min(int(signal.quantity),max_quantity)
        if quantity<=0: return None
        existing=db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.strategy_id==key,StrategyAutoPaperPosition.symbol==signal.symbol.strip().upper(),StrategyAutoPaperPosition.status=="ACTIVE").first()
        if existing is not None: return existing
        pnl=(float(signal.current_price)-float(signal.entry_price))*quantity
        position=StrategyAutoPaperPosition(strategy_id=key,symbol=signal.symbol.strip().upper(),entry_price=float(signal.entry_price),current_price=float(signal.current_price),quantity=quantity,pnl=round(pnl,8),entry_pnl=round(pnl,8),capital_allocated=float(setting.paper_amount),expiry=signal.expiry,first_expiry=signal.expiry,status="ACTIVE",metadata_json=json.dumps(signal.metadata or {},sort_keys=True,default=str),legs_json="[]")
        db.add(position); db.commit(); db.refresh(position); return position

    def open_from_alert(self, db: Session, event, user_id: int) -> StrategyAutoPaperPosition | None:
        strategy=str(event.strategy_id).strip().lower()
        if strategy not in STRATEGIES: return None
        setting=db.query(GlobalPaperSetting).filter(GlobalPaperSetting.user_id==int(user_id)).first()
        if setting is None or not setting.enabled or setting.emergency_stop or float(setting.paper_amount or 0)<=0: return None
        meta=dict(event.metadata or {}); key=str(meta.get("paper_trade_key") or event.event_id).strip()
        if not key: return None
        existing=db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.trade_key==key,StrategyAutoPaperPosition.status=="ACTIVE").first()
        if existing is not None: return existing
        gross=float(meta.get("net_profit",meta.get("gross_profit",meta.get("gross_pnl",0.0))) or 0.0)
        lots=max(1,int(meta.get("lots",meta.get("alert_lots",1)) or 1)); expiry=meta.get("expiry"); first=meta.get("first_expiry",expiry)
        row=StrategyAutoPaperPosition(user_id=int(user_id),trade_key=key,alert_event_id=str(event.event_id),strategy_id=strategy,symbol=str(event.symbol).strip().upper(),direction=str(meta.get("direction") or "") or None,entry_price=1.0,current_price=1.0,quantity=lots,pnl=round(gross,8),entry_pnl=round(gross,8),capital_allocated=float(setting.paper_amount),expiry=str(expiry) if expiry else None,first_expiry=str(first) if first else None,status="ACTIVE",metadata_json=json.dumps(meta,sort_keys=True,default=str),legs_json=json.dumps(meta.get("legs",[]),sort_keys=True,default=str))
        db.add(row); db.commit(); db.refresh(row); return row

    def open_for_enabled_users(self, db: Session, event) -> int:
        key=str(event.metadata.get("paper_trade_key") or event.event_id)
        settings_rows=db.query(GlobalPaperSetting).filter(GlobalPaperSetting.enabled.is_(True),GlobalPaperSetting.emergency_stop.is_(False)).all(); created=0
        for setting in settings_rows:
            existing=db.query(StrategyAutoPaperPosition.id).filter(StrategyAutoPaperPosition.trade_key==key,StrategyAutoPaperPosition.status=="ACTIVE",StrategyAutoPaperPosition.user_id==int(setting.user_id)).first()
            if existing is None and self.open_from_alert(db,event,int(setting.user_id)) is not None: created+=1
        return created

    def positions(self, db: Session, strategy_id: str | None = None) -> list[StrategyAutoPaperPosition]:
        q=db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.status=="ACTIVE")
        if strategy_id: q=q.filter(StrategyAutoPaperPosition.strategy_id==strategy_id.strip().lower())
        return q.order_by(StrategyAutoPaperPosition.pnl.desc(),StrategyAutoPaperPosition.opened_at.asc()).all()

    def completed(self, db: Session, user_id: int | None = None, limit: int = 200) -> list[StrategyAutoPaperPosition]:
        q=db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.status=="CLOSED")
        if user_id is not None: q=q.filter(StrategyAutoPaperPosition.user_id==int(user_id))
        return q.order_by(StrategyAutoPaperPosition.realized_pnl.desc(),StrategyAutoPaperPosition.closed_at.desc()).limit(max(1,min(int(limit),1000))).all()

    def update_mark(self, db: Session, position_id: int, current_price: float) -> bool:
        """Update an active paper position from its latest mark price."""
        row = db.query(StrategyAutoPaperPosition).filter(
            StrategyAutoPaperPosition.id == int(position_id),
            StrategyAutoPaperPosition.status == "ACTIVE",
        ).first()
        if row is None:
            return False
        price = float(current_price)
        row.current_price = price
        row.pnl = round((price - float(row.entry_price)) * int(row.quantity), 8)
        row.pnl_pct = round((row.pnl / row.capital_allocated * 100.0) if row.capital_allocated else 0.0, 8)
        db.commit()
        return True

    def update_pnl(self, db: Session, trade_key: str, pnl: float) -> bool:
        row=db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.trade_key==trade_key,StrategyAutoPaperPosition.status=="ACTIVE").first()
        if row is None: return False
        row.pnl=round(float(pnl),8); row.pnl_pct=round((row.pnl/row.capital_allocated*100.0) if row.capital_allocated else 0.0,8); return True

    def sync_marks(self, db: Session, marks) -> int:
        changed=0
        for mark in marks:
            if self.update_pnl(db,str(mark.get("trade_key") or ""),float(mark.get("pnl",0) or 0)): changed+=1
        if changed: db.commit()
        return changed

    def close(self, db: Session, position_id: int, reason: str="MANUAL_CLOSE") -> StrategyAutoPaperPosition:
        row=db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.id==int(position_id),StrategyAutoPaperPosition.status=="ACTIVE").first()
        if row is None: raise LookupError("paper-auto position not found")
        row.status="CLOSED"; row.realized_pnl=round(float(row.pnl),8); row.closed_at=_now(); row.exit_reason=reason.upper()[:64]; db.commit(); db.refresh(row); return row

    def close_expired(self, db: Session) -> int:
        today=datetime.now(timezone.utc).date(); closed=0
        for row in self.positions(db):
            expiry=_parse_date(row.first_expiry or row.expiry)
            if expiry is not None and today>=expiry:
                row.status="CLOSED"; row.realized_pnl=round(float(row.pnl),8); row.closed_at=_now(); row.exit_reason="EXPIRY_CLOSE"; closed+=1
        if closed: db.commit()
        return closed

def position_payload(position):
    try: metadata=json.loads(position.metadata_json or "{}")
    except Exception: metadata={}
    try: legs=json.loads(position.legs_json or "[]")
    except Exception: legs=[]
    return {"id":position.id,"trade_key":position.trade_key,"alert_event_id":position.alert_event_id,"strategy":position.strategy_id,"symbol":position.symbol,"direction":position.direction,"entry":position.entry_price,"current":position.current_price,"quantity":position.quantity,"pnl":position.pnl,"entry_pnl":position.entry_pnl,"realized_pnl":position.realized_pnl,"pnl_pct":position.pnl_pct,"capital_allocated":position.capital_allocated,"expiry":position.expiry,"first_expiry":position.first_expiry,"status":position.status,"legs":legs,"metadata":metadata,"opened_at":position.opened_at,"closed_at":position.closed_at,"exit_reason":position.exit_reason,"emergency_closed":position.emergency_closed}
