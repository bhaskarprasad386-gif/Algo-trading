from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    mobile_number: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TradingAccount(Base):
    __tablename__ = "trading_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="PAPER", nullable=False)
    virtual_balance: Mapped[float] = mapped_column(Float, default=1_000_000.0, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_user", "user_id"),
        Index("ix_password_reset_tokens_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    instrument_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Tick(Base):
    __tablename__ = "ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    ltp: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (Index("uq_candle_identity", "token", "timeframe", "timestamp", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)


class HistoricalMarketBar(Base):
    __tablename__ = "historical_market_bars"
    __table_args__ = (
        Index("uq_historical_bar_identity", "instrument_key", "timestamp", unique=True),
        Index("ix_historical_bar_symbol_timestamp", "symbol", "timestamp"),
        Index("ix_historical_bar_contract_timestamp", "contract_month", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    instrument_key: Mapped[str] = mapped_column(String(192), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    contract_month: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    data_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class BacktestDataCoverage(Base):
    __tablename__ = "backtest_data_coverage"
    __table_args__ = (
        Index("uq_backtest_coverage_identity", "instrument_key", "timeframe", "start_date", "end_date", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    instrument_key: Mapped[str] = mapped_column(String(192), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    contract_month: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    timeframe: Mapped[str] = mapped_column(String(16), default="1m", nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    data_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    transaction_type: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_price: Mapped[float] = mapped_column(Float, default=0.0)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class BacktestJobResultChunk(Base):
    """Durable per-symbol result chunks for large background backtests.

    A chunk is committed as soon as one symbol finishes, so a full-year/full-F&O
    run never needs to retain every symbol result in one in-memory JSON object.
    """

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


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
