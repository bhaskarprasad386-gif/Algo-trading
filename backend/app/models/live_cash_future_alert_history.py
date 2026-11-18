from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LiveCashFutureAlertHistory(Base):
    """Durable scanner-generated alert events retained for 30 days."""

    __tablename__ = "live_cash_future_alert_history"
    __table_args__ = (
        Index("ix_live_cf_alert_observed_at", "observed_at"),
        Index("ix_live_cf_alert_symbol_month", "symbol", "contract_month", "observed_at"),
        Index("uq_live_cf_alert_identity", "symbol", "contract_month", "timestamp_ns", "event", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    contract_month: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    cash_ask: Mapped[float] = mapped_column(Float, nullable=False)
    future_bid: Mapped[float] = mapped_column(Float, nullable=False)
    gap: Mapped[float] = mapped_column(Float, nullable=False)
    gap_pct: Mapped[float] = mapped_column(Float, nullable=False)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alert_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_gap_pct: Mapped[float] = mapped_column(Float, nullable=False)
    annualized_gap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    stable_observations: Mapped[int] = mapped_column(Integer, default=1)
