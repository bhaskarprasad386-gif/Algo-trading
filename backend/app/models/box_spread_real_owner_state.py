"""Durable single-owner metadata for Box Spread real-broker safety."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BoxSpreadRealOwnerState(Base):
    """Persist only account binding and fail-closed safety flags.

    Broker credentials and live session tokens must never be stored here.
    """

    __tablename__ = "box_spread_real_owner_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_key: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default="box-spread-real-owner"
    )
    broker_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    account_fingerprint: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    real_trading_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


__all__ = ["BoxSpreadRealOwnerState"]
