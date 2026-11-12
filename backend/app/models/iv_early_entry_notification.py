"""Durable Telegram notification outbox for IV early-entry alert events."""
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVEarlyEntryNotificationOutbox(Base):
    __tablename__ = "iv_early_entry_notification_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", "channel", name="uq_iv_alert_outbox_event_channel"),
        Index("ix_iv_alert_outbox_due", "status", "next_attempt_ns"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("iv_early_entry_alert_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="TELEGRAM")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_until_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")


__all__ = ["IVEarlyEntryNotificationOutbox"]
