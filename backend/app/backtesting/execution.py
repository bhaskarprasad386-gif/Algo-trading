"""Deterministic execution simulator for universal backtests."""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class ExecutionSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass(frozen=True)
class SimOrder:
    order_id: str
    instrument: str
    side: ExecutionSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    submitted_at_ns: int = 0

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.instrument.strip():
            raise ValueError("order_id and instrument are required")
        if self.quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        if self.submitted_at_ns < 0:
            raise ValueError("submitted_at_ns cannot be negative")
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        if self.order_type == OrderType.STOP and self.stop_price is None:
            raise ValueError("stop_price is required for STOP orders")


@dataclass(frozen=True)
class SimFill:
    order_id: str
    instrument: str
    side: ExecutionSide
    quantity: int
    price: float
    filled_at_ns: int
    fee: float = 0.0


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_bps: float = 0.0
    latency_ns: int = 0
    fee_per_unit: float = 0.0

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.latency_ns < 0 or self.fee_per_unit < 0:
            raise ValueError("execution costs and latency cannot be negative")


class ExecutionSimulator:
    """Small deterministic simulator; future depth-aware matching extends this contract."""

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()

    def execute(self, order: SimOrder, market_price: float, timestamp_ns: int) -> SimFill:
        if market_price <= 0:
            raise ValueError("market_price must be greater than zero")
        if timestamp_ns < order.submitted_at_ns:
            raise ValueError("fill timestamp cannot precede order submission")
        fill_time = timestamp_ns + self.config.latency_ns
        direction = 1 if order.side == ExecutionSide.BUY else -1
        price = market_price * (1 + direction * self.config.slippage_bps / 10_000)
        return SimFill(
            order_id=order.order_id,
            instrument=order.instrument,
            side=order.side,
            quantity=order.quantity,
            price=price,
            filled_at_ns=fill_time,
            fee=order.quantity * self.config.fee_per_unit,
        )

    def execute_many(self, orders: Iterable[tuple[SimOrder, float, int]]) -> tuple[SimFill, ...]:
        return tuple(self.execute(order, price, timestamp_ns) for order, price, timestamp_ns in orders)
