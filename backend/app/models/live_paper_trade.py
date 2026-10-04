from sqlalchemy import Column, Integer, String, Float, DateTime, Text, UniqueConstraint
from datetime import datetime, timezone
from app.core.database import Base

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class LivePaperTrade(Base):
    __tablename__ = "live_paper_trades"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    strategy_id = Column(String(128), nullable=False, index=True)
    symbol = Column(String(128), nullable=False, index=True)
    event_id = Column(String(512), nullable=False, index=True)
    direction = Column(String(32), nullable=False)
    expiry = Column(String(64), nullable=True, index=True)
    earliest_expiry = Column(String(64), nullable=True, index=True)
    lot_size = Column(Integer, nullable=False, default=1)
    lots = Column(Integer, nullable=False, default=1)
    entry_edge = Column(Float, nullable=False, default=0.0)
    current_edge = Column(Float, nullable=False, default=0.0)
    capital_used = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    pnl_pct = Column(Float, nullable=False, default=0.0)
    legs_json = Column(Text, nullable=False, default="[]")
    metadata_json = Column(Text, nullable=False, default="{}")
    status = Column(String(32), nullable=False, default="ONGOING", index=True)
    exit_reason = Column(String(64), nullable=True)
    opened_at = Column(DateTime, nullable=False, default=utc_now)
    closed_at = Column(DateTime, nullable=True)
    last_mark_at = Column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_live_paper_user_event"),)
