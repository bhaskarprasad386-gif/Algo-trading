from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class LiveSyntheticAlertHistory(Base):
    """Durable synthetic-arbitrage alerts retained for the configured window."""
    __tablename__ = "live_synthetic_alert_history"
    __table_args__ = (
        Index("ix_live_syn_alert_observed_at", "observed_at"),
        Index("ix_live_syn_alert_symbol", "symbol", "observed_at"),
        Index("uq_live_syn_alert_identity", "symbol", "expiry", "timestamp_ns", "strike", "direction", unique=True),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    instrument_class: Mapped[str] = mapped_column(String(16), nullable=False)
    expiry: Mapped[int] = mapped_column(Integer, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    executable_edge: Mapped[float] = mapped_column(Float, nullable=False)
    edge_per_lot: Mapped[float] = mapped_column(Float, nullable=False)
    gross_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
