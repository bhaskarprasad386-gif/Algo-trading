from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BoxSpreadIndexSelection(Base):
    """Persist the active manual Box Spread index universe as a singleton."""

    __tablename__ = "box_spread_index_selection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbols_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default='["NIFTY","BANKNIFTY","MIDCPNIFTY","SENSEX"]',
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
