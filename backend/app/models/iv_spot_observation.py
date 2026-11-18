"""Durable source-timestamped IV scanner index spot ticks."""
from sqlalchemy import BigInteger, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVSpotObservation(Base):
    """One accepted index spot tick, idempotent by symbol and exchange time."""

    __tablename__ = "iv_spot_observations"

    underlying: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_timestamp_ns: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    received_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


__all__ = ["IVSpotObservation"]
