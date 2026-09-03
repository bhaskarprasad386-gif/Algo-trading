from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BacktestJob(Base):
    __tablename__ = "backtest_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="queued")
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    contract_month: Mapped[str] = mapped_column(String(64), index=True)
    requested_days: Mapped[int] = mapped_column(Integer)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    symbols_processed: Mapped[int] = mapped_column(Integer, default=0)
    symbols_total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
