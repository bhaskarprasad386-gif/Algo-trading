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
    queue_ahead_quantity: int = 0

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.instrument.strip():
            raise ValueError("order_id and instrument are required")
        if self.quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        if self.submitted_at_ns < 0:
            raise ValueError("submitted_at_ns cannot be negative")
        if self.queue_ahead_quantity < 0:
            raise ValueError("queue_ahead_quantity cannot be negative")
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        if self.order_type == OrderType.STOP and self.stop_price is None:
            raise ValueError("stop_price is required for STOP orders")


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: int

    def __post_init__(self) -> None:
        if self.price <= 0 or self.quantity < 0:
            raise ValueError("depth price must be positive and quantity non-negative")


@dataclass(frozen=True)
class OrderBook:
    """Point-in-time executable order-book snapshot, best level first."""

    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()

    def __post_init__(self) -> None:
        bid_prices = tuple(x.price for x in self.bids)
        ask_prices = tuple(x.price for x in self.asks)
        if bid_prices != tuple(sorted(bid_prices, reverse=True)):
            raise ValueError("bids must be ordered best-to-worst")
        if ask_prices != tuple(sorted(ask_prices)):
            raise ValueError("asks must be ordered best-to-worst")


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
class ExecutionResult:
    fills: tuple[SimFill, ...]
    remaining_quantity: int
    rejected: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_bps: float = 0.0
    latency_ns: int = 0
    fee_per_unit: float = 0.0
    allow_partial_fills: bool = True

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.latency_ns < 0 or self.fee_per_unit < 0:
            raise ValueError("execution costs and latency cannot be negative")


class ExecutionSimulator:
    """Execution model supporting point-in-time depth and conservative queueing."""

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
        return SimFill(order.order_id, order.instrument, order.side, order.quantity, price, fill_time,
                       order.quantity * self.config.fee_per_unit)

    @staticmethod
    def _executable_levels(order: SimOrder, book: OrderBook) -> tuple[DepthLevel, ...]:
        levels = book.asks if order.side == ExecutionSide.BUY else book.bids
        if order.order_type != OrderType.LIMIT:
            return levels
        if order.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        accepted = []
        for level in levels:
            if order.side == ExecutionSide.BUY and level.price > order.limit_price:
                break
            if order.side == ExecutionSide.SELL and level.price < order.limit_price:
                break
            accepted.append(level)
        return tuple(accepted)

    def execute_depth(self, order: SimOrder, book: OrderBook, timestamp_ns: int) -> ExecutionResult:
        """Consume displayed depth, optionally behind an explicit observed queue ahead.

        `queue_ahead_quantity` is an input from observed order/queue evidence. It is
        never inferred from an unrelated instrument or fabricated from time alone.
        FOK performs an atomic executable-quantity check before producing fills.
        """
        if timestamp_ns < order.submitted_at_ns:
            raise ValueError("fill timestamp cannot precede order submission")
        levels = self._executable_levels(order, book)
        if not levels:
            return ExecutionResult((), order.quantity, True, "no executable depth")

        executable = sum(level.quantity for level in levels)
        effective_executable = max(0, executable - order.queue_ahead_quantity)
        if not self.config.allow_partial_fills and effective_executable < order.quantity:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth")

        remaining = order.quantity
        queue_remaining = order.queue_ahead_quantity
        fills: list[SimFill] = []
        for level in levels:
            if remaining <= 0:
                break
            if level.quantity <= 0:
                continue
            consumed_for_queue = min(queue_remaining, level.quantity)
            queue_remaining -= consumed_for_queue
            available = level.quantity - consumed_for_queue
            if available <= 0:
                continue
            take = min(remaining, available)
            fills.append(SimFill(order.order_id, order.instrument, order.side, take, level.price,
                                 timestamp_ns + self.config.latency_ns, take * self.config.fee_per_unit))
            remaining -= take

        if not fills:
            return ExecutionResult((), order.quantity, True, "queue ahead not depleted")
        return ExecutionResult(tuple(fills), remaining, False, None if remaining == 0 else "partial fill")

    def execute_many(self, orders: Iterable[tuple[SimOrder, float, int]]) -> tuple[SimFill, ...]:
        return tuple(self.execute(order, price, timestamp_ns) for order, price, timestamp_ns in orders)
