"""Atomic idempotency claims for Box Spread snapshot writes."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LiveBoxSpreadSnapshotKey(Base):
    """A unique database claim for one quote/strategy identity."""

    __tablename__ = "live_box_spread_snapshot_keys"

    snapshot_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


__all__ = ["LiveBoxSpreadSnapshotKey"]
