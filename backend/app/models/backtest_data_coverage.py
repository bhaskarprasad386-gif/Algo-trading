from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BacktestDataCoverage(Base):
    """Catalog of validated local one-minute historical-data ranges."""

    __tablename__ = "backtest_data_coverage"
    __table_args__ = (
        Index(
            "uq_backtest_coverage_identity",
            "instrument_key",
            "timeframe",
            "start_date",
            "end_date",
            unique=True,
        ),
        Index("ix_backtest_coverage_lookup", "instrument_key", "timeframe", "start_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_key: Mapped[str] = mapped_column(String(256), index=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    segment: Mapped[str] = mapped_column(String(32), index=True)
    contract_month: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), default="1m")
    start_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    data_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validated: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
