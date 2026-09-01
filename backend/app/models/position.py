from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from datetime import datetime
from app.core.database import Base


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=True)
    symbol = Column(String, index=True, nullable=False)
    token = Column(String, nullable=True)
    exchange = Column(String, nullable=True)

    quantity = Column(Integer, default=0)
    average_price = Column(Float, default=0.0)
    last_price = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)

    product_type = Column(String, default="INTRADAY")
    is_paper = Column(Boolean, default=True)
    is_open = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
