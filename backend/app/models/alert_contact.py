from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from app.core.database import Base

class AlertContact(Base):
    __tablename__ = "alert_contacts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    label = Column(String(64), nullable=False, default="Primary")
    mobile_number = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    sms_enabled = Column(Boolean, nullable=False, default=False)
    app_enabled = Column(Boolean, nullable=False, default=True)
    whatsapp_enabled = Column(Boolean, nullable=False, default=False)
    email_enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
