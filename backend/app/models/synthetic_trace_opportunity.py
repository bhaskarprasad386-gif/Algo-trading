"""Durable user-captured synthetic arbitrage trace snapshots."""
from datetime import datetime
from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class SyntheticTraceOpportunity(Base):
    __tablename__ = "synthetic_trace_opportunities"

    trace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    traced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    expiry: Mapped[str] = mapped_column(String(32), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_edge: Mapped[float] = mapped_column(Float, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
