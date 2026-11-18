from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LiveCashFutureScannerResult(Base):
    """Durable eligible live Cash-Future scanner results retained for replay/analysis."""

    __tablename__ = "live_cash_future_scanner_results"
    __table_args__ = (
        Index("uq_live_cf_scanner_result_identity", "symbol", "contract_month", "timestamp_ns", unique=True),
        Index("ix_live_cf_scanner_result_observed_at", "observed_at"),
        Index("ix_live_cf_scanner_result_symbol_month", "symbol", "contract_month", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    contract_month: Mapped[str] = mapped_column(String(32), index=True)
    timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    cash_ltp: Mapped[float] = mapped_column(Float)
    future_ltp: Mapped[float] = mapped_column(Float)
    cash_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    future_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    future_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_bid_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_ask_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    future_bid_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    future_ask_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap: Mapped[float] = mapped_column(Float)
    gap_pct: Mapped[float] = mapped_column(Float)
    cash_day_high: Mapped[float] = mapped_column(Float)
    cash_day_low: Mapped[float] = mapped_column(Float)
    future_day_high: Mapped[float] = mapped_column(Float)
    future_day_low: Mapped[float] = mapped_column(Float)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_lot_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    net_gap: Mapped[float] = mapped_column(Float)
    net_gap_pct: Mapped[float] = mapped_column(Float)
    annualized_gap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stable_observations: Mapped[int] = mapped_column(Integer, default=1)
    capacity_lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capacity_notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifecycle: Mapped[str] = mapped_column(String(32))
    reason_codes: Mapped[str] = mapped_column(Text, default="")
    observation_ref: Mapped[str] = mapped_column(String(256))
    alert_event: Mapped[str | None] = mapped_column(String(32), nullable=True)
