from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, Integer
from app.core.database import Base

class GlobalPaperSetting(Base):
    __tablename__ = "global_paper_settings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    paper_amount = Column(Float, nullable=False, default=10000000.0)
    emergency_stop = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
