"""Durable observations of live Box Spread rows, independent of alert delivery."""
from datetime import datetime, timezone
from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class LiveBoxSpreadSnapshot(Base):
    __tablename__ = "live_box_spread_snapshots"
    __table_args__ = (
        Index("ix_box_snapshot_observed_at", "observed_at"),
        Index("ix_box_snapshot_symbol_time", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    expiry: Mapped[int] = mapped_column(Integer, nullable=False)
    low_strike: Mapped[float] = mapped_column(Float, nullable=False)
    high_strike: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = ["LiveBoxSpreadSnapshot"]
