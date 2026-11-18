from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String, default="INFO")  # INFO / WARNING / ERROR / CRITICAL
    module = Column(String, nullable=True)  # auth / market_data / order etc.
    message = Column(Text, nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
