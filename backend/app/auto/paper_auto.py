"""Strategy-neutral global paper-auto gate and lifecycle ledger.

This layer never creates live broker orders. Strategies submit normalized
qualifying opportunities; the global gate decides whether automatic paper
entry is allowed and records a bounded, durable lifecycle position.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.strategy_auto_paper_position import StrategyAutoPaperPosition
from app.models.global_paper_setting import GlobalPaperSetting

@dataclass(frozen=True)
class AutoSignal:
    strategy_id: str
    symbol: str
    entry_price: float
    current_price: float
    quantity: int = 1
    expiry: str | None = None
    metadata: dict | None = None

class GlobalPaperAutoService:
    def is_enabled(self, db: Session, strategy_id: str) -> bool:
        setting = db.query(GlobalPaperSetting).first()
        return bool(setting and setting.enabled and not setting.emergency_stop)

    def set_enabled(self, db: Session, strategy_id: str, enabled: bool) -> GlobalPaperSetting:
        """Set the single global paper-auto switch; strategy_id is retained for API compatibility."""
        setting = db.query(GlobalPaperSetting).first()
        if setting is None:
            setting = GlobalPaperSetting(user_id=1, enabled=bool(enabled))
            db.add(setting)
        else:
            setting.enabled = bool(enabled)
            setting.emergency_stop = False
            setting.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        db.refresh(setting)
        return setting

    def qualify_and_enter(self, db: Session, signal: AutoSignal) -> StrategyAutoPaperPosition | None:
        if not self.is_enabled(db, signal.strategy_id):
            return None
        if signal.quantity <= 0 or signal.entry_price <= 0 or signal.current_price <= 0:
            raise ValueError("paper-auto signal prices and quantity must be positive")
        key = signal.strategy_id.strip().lower()
        setting = db.query(GlobalPaperSetting).first()
        if setting is None or float(setting.paper_amount) <= 0:
            return None
        max_quantity = int(float(setting.paper_amount) // float(signal.entry_price))
        quantity = min(int(signal.quantity), max_quantity)
        if quantity <= 0:
            return None
        existing = db.query(StrategyAutoPaperPosition).filter(
            StrategyAutoPaperPosition.strategy_id == key,
            StrategyAutoPaperPosition.symbol == signal.symbol.strip().upper(),
            StrategyAutoPaperPosition.status == "ACTIVE",
        ).first()
        if existing is not None:
            return existing
        pnl = (float(signal.current_price) - float(signal.entry_price)) * quantity
        position = StrategyAutoPaperPosition(
            strategy_id=key,
            symbol=signal.symbol.strip().upper(),
            entry_price=float(signal.entry_price),
            current_price=float(signal.current_price),
            quantity=quantity,
            pnl=round(pnl, 8),
            expiry=signal.expiry,
            status="ACTIVE",
            metadata_json=json.dumps(signal.metadata or {}, sort_keys=True, default=str),
        )
        db.add(position)
        db.commit()
        db.refresh(position)
        return position

    def positions(self, db: Session, strategy_id: str | None = None) -> list[StrategyAutoPaperPosition]:
        query = db.query(StrategyAutoPaperPosition).filter(
            StrategyAutoPaperPosition.status == "ACTIVE"
        )
        if strategy_id:
            query = query.filter(StrategyAutoPaperPosition.strategy_id == strategy_id.strip().lower())
        return query.order_by(StrategyAutoPaperPosition.opened_at.desc()).all()

    def update_mark(self, db: Session, position_id: int, current_price: float) -> StrategyAutoPaperPosition:
        position = db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.id == position_id).first()
        if position is None:
            raise LookupError("paper-auto position not found")
        position.current_price = float(current_price)
        position.pnl = round((position.current_price - position.entry_price) * position.quantity, 8)
        db.commit()
        db.refresh(position)
        return position

    def close(self, db: Session, position_id: int) -> StrategyAutoPaperPosition:
        position = db.query(StrategyAutoPaperPosition).filter(StrategyAutoPaperPosition.id == position_id).first()
        if position is None:
            raise LookupError("paper-auto position not found")
        position.status = "CLOSED"
        position.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        db.refresh(position)
        return position

def position_payload(position: StrategyAutoPaperPosition) -> dict:
    return {
        "id": position.id,
        "strategy": position.strategy_id,
        "symbol": position.symbol,
        "entry": position.entry_price,
        "current": position.current_price,
        "quantity": position.quantity,
        "pnl": position.pnl,
        "expiry": position.expiry,
        "status": position.status,
        "opened_at": position.opened_at,
        "closed_at": position.closed_at,
    }
