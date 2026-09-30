from sqlalchemy import Boolean, Column, Integer, String, DateTime, UniqueConstraint
from datetime import datetime, timezone
from app.core.database import Base

class StrategyAutoSetting(Base):
    __tablename__ = "strategy_auto_settings"
    __table_args__ = (UniqueConstraint("strategy_id", name="uq_strategy_auto_setting_strategy"),)
    id = Column(Integer, primary_key=True)
    strategy_id = Column(String(128), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
