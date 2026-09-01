from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from datetime import datetime
from app.core.database import Base


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    name = Column(String, nullable=True)
    token = Column(String, unique=True, index=True, nullable=False)
    exchange = Column(String, index=True, nullable=False)  # NSE, BSE, NFO, MCX
    instrument_type = Column(String, nullable=True)  # EQ, FUT, CE, PE, INDEX
    segment = Column(String, nullable=True)
    lot_size = Column(Integer, default=1)
    tick_size = Column(Float, default=0.05)
    expiry = Column(String, nullable=True)
    strike = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
