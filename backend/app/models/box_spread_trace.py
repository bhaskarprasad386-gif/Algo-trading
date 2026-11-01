"""Immutable Box Spread trade traces and timestamped exit checks."""
from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class BoxSpreadTrace(Base):
    __tablename__ = "box_spread_traces"
    __table_args__ = (
        Index("ix_box_trace_created_at", "created_at"),
        Index("ix_box_trace_identity", "source_identity", unique=True),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    expiry: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_json: Mapped[str] = mapped_column(Text, nullable=False)


class BoxSpreadTraceExitCheck(Base):
    __tablename__ = "box_spread_trace_exit_checks"
    __table_args__ = (Index("ix_box_trace_exit_trace_checked", "trace_id", "checked_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_json: Mapped[str] = mapped_column(Text, nullable=False)
