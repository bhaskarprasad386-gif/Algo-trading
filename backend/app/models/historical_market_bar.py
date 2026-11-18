from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HistoricalMarketBar(Base):
    """Durable one-minute OHLC/market-data row for incremental backtesting."""

    __tablename__ = "historical_market_bars"
    __table_args__ = (
        Index(
            "uq_historical_bar_identity",
            "instrument_key",
            "timestamp",
            unique=True,
        ),
        Index("ix_historical_bar_lookup", "instrument_key", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_key: Mapped[str] = mapped_column(String(256), index=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    segment: Mapped[str] = mapped_column(String(32), index=True)
    instrument_type: Mapped[str] = mapped_column(String(32), index=True)
    contract_month: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    expiry_date: Mapped[date | datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    data_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
