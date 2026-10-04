from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index, text

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Position(Base):
    __tablename__ = "positions"
    # Only one live paper position may exist per user/symbol. Closed and
    # non-paper history remains repeatable; the database enforces the active
    # paper-position invariant instead of relying on a check-then-insert race.
    __table_args__ = (
        Index(
            "uq_positions_user_symbol_active_paper",
            "user_id",
            "symbol",
            unique=True,
            sqlite_where=text("is_paper = 1 AND is_open = 1"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=True)
    symbol = Column(String, index=True, nullable=False)
    token = Column(String, nullable=True)
    exchange = Column(String, nullable=True)

    quantity = Column(Integer, default=0)
    average_price = Column(Float, default=0.0)
    last_price = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)
    stop_loss = Column(Float, nullable=True)
    target = Column(Float, nullable=True)

    product_type = Column(String, default="INTRADAY")
    is_paper = Column(Boolean, default=True)
    is_open = Column(Boolean, default=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
