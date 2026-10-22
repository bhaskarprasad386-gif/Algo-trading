"""Durable audit history for explicit user-triggered synthetic trace checks."""
from datetime import datetime
from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class SyntheticTraceStatusCheck(Base):
    __tablename__ = "synthetic_trace_status_checks"
    __table_args__ = (
        Index("ix_synthetic_trace_checks_trace_time", "trace_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    expiry: Mapped[str] = mapped_column(String(32), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_edge: Mapped[float] = mapped_column(Float, nullable=True)
    current_edge: Mapped[float] = mapped_column(Float, nullable=True)
    edge_change: Mapped[float] = mapped_column(Float, nullable=True)
    future_bid: Mapped[float] = mapped_column(Float, nullable=True)
    future_ask: Mapped[float] = mapped_column(Float, nullable=True)
    call_bid: Mapped[float] = mapped_column(Float, nullable=True)
    call_ask: Mapped[float] = mapped_column(Float, nullable=True)
    put_bid: Mapped[float] = mapped_column(Float, nullable=True)
    put_ask: Mapped[float] = mapped_column(Float, nullable=True)
    synthetic_bid: Mapped[float] = mapped_column(Float, nullable=True)
    synthetic_ask: Mapped[float] = mapped_column(Float, nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pnl_per_unit: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_per_lot: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNAVAILABLE")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
