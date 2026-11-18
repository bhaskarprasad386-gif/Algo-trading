from sqlalchemy import BigInteger, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IVSpotLastPrice(Base):
    """Durable last broker-traded index spot, independent of runner lifecycle."""

    __tablename__ = "iv_spot_last_price"

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
