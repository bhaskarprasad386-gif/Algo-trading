from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class LiveCalendarSpreadAlertHistory(Base):
    """Qualifying Calendar Spread alerts retained for the configured 90-day window."""
    __tablename__ = "live_calendar_spread_alert_history"
    __table_args__ = (
        Index("ix_live_calendar_alert_observed_at", "observed_at"),
        Index("uq_live_calendar_alert_identity", "underlying", "exchange", "near_contract_month", "far_contract_month", "timestamp_ns", unique=True),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    underlying: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    near_contract_month: Mapped[str] = mapped_column(String(32), nullable=False)
    far_contract_month: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(64), nullable=False)
    edge_long: Mapped[float] = mapped_column(Float, nullable=False)
    edge_short: Mapped[float] = mapped_column(Float, nullable=False)
    gap_points: Mapped[float] = mapped_column(Float, nullable=False)
    gross_profit: Mapped[float] = mapped_column(Float, nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
