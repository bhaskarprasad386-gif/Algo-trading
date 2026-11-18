from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    token_hash = Column(String(256), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=utc_now)
