from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CashFutureHistory(Base):
    """Persisted Cash-Future observations for history, graphing and backtests."""

    __tablename__ = "cash_future_history"
    __table_args__ = (
        Index(
            "uq_cash_future_history_identity",
            "symbol",
            "contract_month",
            "timestamp",
            unique=True,
        ),
        Index("ix_cash_future_history_lookup", "symbol", "contract_month", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    contract_month: Mapped[str] = mapped_column(String(32), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    cash_price: Mapped[float] = mapped_column(Float)
    future_price: Mapped[float] = mapped_column(Float)
    gap: Mapped[float] = mapped_column(Float)
    gap_pct: Mapped[float] = mapped_column(Float)
    lot_size: Mapped[int] = mapped_column(Integer)
    margin_required: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi: Mapped[float | None] = mapped_column(Float, nullable=True)
    charges: Mapped[float] = mapped_column(Float, default=0.0)
    funding_cost: Mapped[float] = mapped_column(Float, default=0.0)
    net_profit: Mapped[float] = mapped_column(Float, default=0.0)
    roi_pct: Mapped[float] = mapped_column(Float, default=0.0)
