from __future__ import annotations

from sqlalchemy import BigInteger, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ExecutionIntentRecord(Base):
    __tablename__ = "execution_intents"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_execution_intent_account_key"),
        Index("ix_execution_intents_account_status", "account_id", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intent_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    legs: Mapped[list["ExecutionLegRecord"]] = relationship(back_populates="intent", cascade="all, delete-orphan", order_by="ExecutionLegRecord.id")
    events: Mapped[list["ExecutionIntentEventRecord"]] = relationship(back_populates="intent", cascade="all, delete-orphan", order_by="ExecutionIntentEventRecord.sequence")
    fills: Mapped[list["ExecutionFillRecord"]] = relationship(back_populates="intent", cascade="all, delete-orphan", order_by="ExecutionFillRecord.id")


class ExecutionLegRecord(Base):
    __tablename__ = "execution_intent_legs"
    __table_args__ = (
        UniqueConstraint("intent_record_id", "leg_id", name="uq_execution_intent_leg_id"),
        Index("ix_execution_intent_legs_instrument", "instrument"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intent_record_id: Mapped[int] = mapped_column(ForeignKey("execution_intents.id", ondelete="CASCADE"), nullable=False)
    leg_id: Mapped[str] = mapped_column(String(128), nullable=False)
    instrument: Mapped[str] = mapped_column(String(192), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intent: Mapped[ExecutionIntentRecord] = relationship(back_populates="legs")


class ExecutionIntentEventRecord(Base):
    __tablename__ = "execution_intent_events"
    __table_args__ = (
        UniqueConstraint("intent_record_id", "sequence", name="uq_execution_intent_event_sequence"),
        Index("ix_execution_intent_events_time", "intent_record_id", "occurred_at_ns"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intent_record_id: Mapped[int] = mapped_column(ForeignKey("execution_intents.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    intent: Mapped[ExecutionIntentRecord] = relationship(back_populates="events")


class ExecutionFillRecord(Base):
    """Immutable broker execution report, uniquely identified per account/broker."""
    __tablename__ = "execution_intent_fills"
    __table_args__ = (
        UniqueConstraint("account_id", "broker", "broker_fill_id", name="uq_execution_fill_broker_identity"),
        Index("ix_execution_intent_fills_intent", "intent_record_id", "leg_record_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intent_record_id: Mapped[int] = mapped_column(ForeignKey("execution_intents.id", ondelete="CASCADE"), nullable=False)
    leg_record_id: Mapped[int] = mapped_column(ForeignKey("execution_intent_legs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_fill_id: Mapped[str] = mapped_column(String(192), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(192), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    occurred_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[ExecutionIntentRecord] = relationship(back_populates="fills")


class ExecutionBrokerOrderRecord(Base):
    """Immutable broker order identity linked to one persisted execution leg."""

    __tablename__ = "execution_broker_orders"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "broker", "broker_order_id",
            name="uq_execution_broker_order_identity",
        ),
        Index("ix_execution_broker_orders_intent_leg", "intent_record_id", "leg_record_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intent_record_id: Mapped[int] = mapped_column(
        ForeignKey("execution_intents.id", ondelete="CASCADE"), nullable=False
    )
    leg_record_id: Mapped[int] = mapped_column(
        ForeignKey("execution_intent_legs.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(192), nullable=False)
    symbol: Mapped[str] = mapped_column(String(192), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String(192), nullable=True)
    broker_status: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
