from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Boolean

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    access_token = Column(String, unique=True, index=True, nullable=False)
    refresh_token = Column(String, nullable=True)
    device_info = Column(String, nullable=True)  # android / web
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
