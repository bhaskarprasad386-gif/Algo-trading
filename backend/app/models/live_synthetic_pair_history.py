"""Durable sampled snapshots from the live synthetic pair monitor."""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class LiveSyntheticPairHistory(Base):
    __tablename__ = "live_synthetic_pair_history"
    __table_args__ = (
        Index("ix_live_syn_pair_history_time", "observed_at"),
        Index("ix_live_syn_pair_history_symbol_time", "underlying", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    timestamp_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    underlying: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    instrument_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expiry: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    future_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    future_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    executable_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_per_lot: Mapped[float | None] = mapped_column(Float, nullable=True)
