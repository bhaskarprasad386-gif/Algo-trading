from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BacktestJobResultChunk(Base):
    """Durable bounded result chunk for large background backtests."""

    __tablename__ = "backtest_job_result_chunks"
    __table_args__ = (
        Index("uq_backtest_job_result_chunk", "job_id", "sequence", unique=True),
        Index("ix_backtest_job_result_chunks_job", "job_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
