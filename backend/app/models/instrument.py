from sqlalchemy import Column, Integer, String, Float
from app.core.database import Base

class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    exchange = Column(String, nullable=False)  # NSE, BSE, NFO
    instrument_type = Column(String, nullable=True) # EQ, FUT, CE, PE
    lot_size = Column(Integer, default=1)
