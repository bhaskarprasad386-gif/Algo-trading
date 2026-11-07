"""Durable IV early-move state transitions for honest historical validation."""
from sqlalchemy import BigInteger, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVSignalEvent(Base):
    """One transition into PRE_MOVE, CONFIRMED or INVALIDATED."""

    __tablename__ = "iv_signal_events"
    __table_args__ = (
        Index(
            "uq_iv_signal_event_identity",
            "stream_id", "state", "direction", "event_timestamp_ns",
            unique=True,
        ),
        Index("ix_iv_signal_event_time", "underlying", "event_timestamp_ns"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(32), nullable=False)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    expiry_type: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    event_timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_timestamp_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spot_timestamp_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spot_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_change_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    spot_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    contracts_confirming: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qualifying_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


__all__ = ["IVSignalEvent"]
