from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from datetime import datetime, timezone
from app.core.database import Base

def utc_now(): return datetime.now(timezone.utc).replace(tzinfo=None)

class LiveCalendarSpreadScannerResult(Base):
    __tablename__ = "live_calendar_spread_scanner_results"
    id = Column(Integer, primary_key=True)
    underlying = Column(String, nullable=False, index=True)
    exchange = Column(String, nullable=False)
    instrument_type = Column(String, nullable=False)
    near_contract_month = Column(String, nullable=False)
    far_contract_month = Column(String, nullable=False)
    timestamp_ns = Column(Integer, nullable=False)
    near_bid = Column(Float, nullable=False); near_ask = Column(Float, nullable=False)
    far_bid = Column(Float, nullable=False); far_ask = Column(Float, nullable=False)
    lot_size = Column(Integer, nullable=False)
    edge_long = Column(Float, nullable=False); edge_short = Column(Float, nullable=False)
    edge_pct_long = Column(Float, nullable=False); edge_pct_short = Column(Float, nullable=False)
    liquidity_qty = Column(Float, nullable=False); capacity_lots = Column(Integer, nullable=False)
    rank_score = Column(Float, nullable=False); observed_at = Column(DateTime, default=utc_now, nullable=False)
    __table_args__=(Index("uq_live_calendar_scanner_identity","underlying","exchange","near_contract_month","far_contract_month","timestamp_ns",unique=True),)
