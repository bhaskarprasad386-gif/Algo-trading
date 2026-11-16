from __future__ import annotations

from sqlalchemy import BigInteger, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BrokerOrderEventRecord(Base):
    """Immutable normalized broker callback, unique within broker/account scope."""

    __tablename__ = "broker_order_events"
    __table_args__ = (
        UniqueConstraint(
            "broker", "account_id", "event_id",
            name="uq_broker_order_event_identity",
        ),
        Index(
            "ix_broker_order_events_order_time",
            "broker", "account_id", "broker_order_id", "occurred_at_ns",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str] = mapped_column(String(192), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(192), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cumulative_filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fill_id: Mapped[str | None] = mapped_column(String(192), nullable=True)
    fill_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    occurred_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
