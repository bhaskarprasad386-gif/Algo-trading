"""Durable source-timestamped IV/OI option observations for validation."""
from sqlalchemy import BigInteger, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVOptionObservation(Base):
    """One accepted broker option quote/IV sample; duplicates are source-time keyed."""

    __tablename__ = "iv_option_observations"
    __table_args__ = (
        Index(
            "uq_iv_option_observation_source",
            "token",
            "source_timestamp_ns",
            unique=True,
        ),
        Index(
            "ix_iv_option_observation_contract_time",
            "underlying",
            "expiry",
            "token",
            "source_timestamp_ns",
        ),
        Index("ix_iv_option_observation_time", "source_timestamp_ns"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    expiry: Mapped[str] = mapped_column(String(32), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    option_type: Mapped[str] = mapped_column(String(2), nullable=False)
    source_timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spot_timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spot_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sampled_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    ltp: Mapped[float | None] = mapped_column(Float, nullable=True)
    mark: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


__all__ = ["IVOptionObservation"]
