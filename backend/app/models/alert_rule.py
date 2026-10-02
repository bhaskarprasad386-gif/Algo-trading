from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from app.core.database import Base

class AlertRule(Base):
    __tablename__ = "alert_rules"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    strategy_id = Column(String(128), nullable=False, index=True)
    min_gross_profit = Column(Float, nullable=False, default=0.0)
    mobile_number = Column(String(32), nullable=False)
    whatsapp_enabled = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    max_loss = Column(Float, nullable=False, default=10000.0)
    max_daily_capital = Column(Float, nullable=False, default=10000000.0)
    max_simultaneous_positions = Column(Integer, nullable=False, default=20)
    cooldown_seconds = Column(Float, nullable=False, default=60.0)
    priority = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
