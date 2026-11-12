"""Durable IV early-entry alert history and cooldown state."""
from sqlalchemy import BigInteger, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVEarlyEntryAlertEvent(Base):
    __tablename__ = "iv_early_entry_alert_events"
    __table_args__ = (
        Index("ix_iv_early_entry_alert_time", "underlying", "expiry", "observed_at_ns"),
        Index("ix_iv_early_entry_alert_strategy", "strategy", "state", "observed_at_ns"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    underlying: Mapped[str] = mapped_column(String(16), nullable=False)
    expiry: Mapped[str] = mapped_column(String(32), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    option_type: Mapped[str] = mapped_column(String(2), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    observed_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_change_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    cooldown_key: Mapped[str] = mapped_column(String(256), nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")


class IVEarlyEntryCooldown(Base):
    __tablename__ = "iv_early_entry_cooldowns"

    cooldown_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    last_alert_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expiry: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)


__all__ = ["IVEarlyEntryAlertEvent", "IVEarlyEntryCooldown"]
