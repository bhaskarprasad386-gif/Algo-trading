from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=True)
    order_id = Column(String, unique=True, index=True, nullable=True)
    broker_order_id = Column(String, nullable=True)

    symbol = Column(String, index=True, nullable=False)
    token = Column(String, nullable=True)
    exchange = Column(String, nullable=True)

    transaction_type = Column(String, nullable=False)  # BUY / SELL
    order_type = Column(String, default="MARKET")  # MARKET / LIMIT
    product_type = Column(String, default="INTRADAY")  # INTRADAY / DELIVERY

    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)
    trigger_price = Column(Float, nullable=True)
    average_price = Column(Float, nullable=True)
    filled_quantity = Column(Integer, default=0)
    average_fill_price = Column(Float, nullable=True)
    time_in_force = Column(String, default="DAY")
    pnl = Column(Float, nullable=True, default=0.0)

    status = Column(String, default="PENDING")  # PENDING / SUBMITTED / EXECUTED / REJECTED / CANCELLED
    is_paper = Column(Boolean, default=True)  # True = paper trading (safe)

    message = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
