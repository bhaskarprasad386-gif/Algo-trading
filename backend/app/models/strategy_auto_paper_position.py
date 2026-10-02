from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean
from datetime import datetime, timezone
from app.core.database import Base

class StrategyAutoPaperPosition(Base):
    __tablename__ = "strategy_auto_paper_positions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    trade_key = Column(String(256), nullable=True, index=True)
    alert_event_id = Column(String(256), nullable=True, index=True)
    strategy_id = Column(String(128), nullable=False, index=True)
    symbol = Column(String(128), nullable=False, index=True)
    direction = Column(String(64), nullable=True)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    pnl = Column(Float, nullable=False, default=0.0)
    entry_pnl = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=False, default=0.0)
    capital_allocated = Column(Float, nullable=False, default=0.0)
    expiry = Column(String(64), nullable=True)
    first_expiry = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="DETECTED", index=True)
    exit_reason = Column(String(64), nullable=True)
    emergency_closed = Column(Boolean, nullable=False, default=False)
    metadata_json = Column(Text, nullable=False, default="{}")
    legs_json = Column(Text, nullable=False, default="[]")
    opened_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    closed_at = Column(DateTime, nullable=True)
