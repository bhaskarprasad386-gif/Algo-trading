from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime
from app.core.database import Base

class OrderLog(Base):
    __tablename__ = "order_logs"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, unique=True, index=True, nullable=True)
    symbol = Column(String, nullable=False)
    transaction_type = Column(String, nullable=False) # BUY / SELL
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)
    status = Column(String, default="PENDING_SAFE_MODE") # सेफ मोड प्लेसहोल्डर
    created_at = Column(DateTime, default=datetime.utcnow)
