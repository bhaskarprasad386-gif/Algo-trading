"""Deterministic execution simulator for universal backtests."""

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable


class ExecutionSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


def _strict_nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


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
    time_in_force: TimeInForce = TimeInForce.DAY

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.instrument.strip():
            raise ValueError("order_id and instrument are required")
        if not isinstance(self.side, ExecutionSide) or not isinstance(self.order_type, OrderType):
            raise ValueError("invalid order side or order type")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        _strict_nonnegative_int(self.submitted_at_ns, "submitted_at_ns")
        _strict_nonnegative_int(self.queue_ahead_quantity, "queue_ahead_quantity")
        if not isinstance(self.time_in_force, TimeInForce):
            raise ValueError("invalid time_in_force")
        if self.order_type == OrderType.LIMIT:
            if self.limit_price is None or not math.isfinite(float(self.limit_price)) or self.limit_price <= 0:
                raise ValueError("limit_price must be finite and positive for LIMIT orders")
        elif self.limit_price is not None:
            raise ValueError("limit_price is only valid for LIMIT orders")
        if self.order_type == OrderType.STOP:
            if self.stop_price is None or not math.isfinite(float(self.stop_price)) or self.stop_price <= 0:
                raise ValueError("stop_price must be finite and positive for STOP orders")
        elif self.stop_price is not None:
            raise ValueError("stop_price is only valid for STOP orders")


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity < 0:
            raise ValueError("depth quantity must be a non-negative integer")
        if not math.isfinite(float(self.price)) or self.price <= 0:
            raise ValueError("depth price must be finite and positive and quantity non-negative")


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
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValueError("crossed order book")


@dataclass(frozen=True)
class QueueEvidence:
    """Observed events that can legitimately advance a resting queue position."""
    price: float
    executed_quantity: int = 0
    cancelled_quantity_ahead: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.price)) or self.price <= 0:
            raise ValueError("queue evidence price must be finite and positive")
        if not isinstance(self.executed_quantity, int) or isinstance(self.executed_quantity, bool) or self.executed_quantity < 0:
            raise ValueError("executed_quantity must be a non-negative integer")
        if not isinstance(self.cancelled_quantity_ahead, int) or isinstance(self.cancelled_quantity_ahead, bool) or self.cancelled_quantity_ahead < 0:
            raise ValueError("cancelled_quantity_ahead must be a non-negative integer")


@dataclass(frozen=True)
class SimFill:
    order_id: str
    instrument: str
    side: ExecutionSide
    quantity: int
    price: float
    filled_at_ns: int
    fee: float = 0.0

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.instrument.strip():
            raise ValueError("fill order_id and instrument are required")
        if not isinstance(self.side, ExecutionSide):
            raise ValueError("invalid fill side")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise ValueError("fill quantity must be a positive integer")
        if not math.isfinite(float(self.price)) or self.price <= 0:
            raise ValueError("fill price must be finite and positive")
        _strict_nonnegative_int(self.filled_at_ns, "filled_at_ns")
        if not math.isfinite(float(self.fee)) or self.fee < 0:
            raise ValueError("fill fee must be finite and non-negative")


@dataclass(frozen=True)
class ExecutionResult:
    fills: tuple[SimFill, ...]
    remaining_quantity: int
    rejected: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class AtomicExecutionResult:
    """All-or-nothing result for a multi-leg execution attempt."""
    fills: tuple[SimFill, ...]
    leg_results: tuple[ExecutionResult, ...]
    rejected: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_bps: float = 0.0
    latency_ns: int = 0
    fee_per_unit: float = 0.0
    allow_partial_fills: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.slippage_bps)) or self.slippage_bps < 0:
            raise ValueError("slippage_bps must be finite and non-negative")
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps must be below 10000")
        _strict_nonnegative_int(self.latency_ns, "latency_ns")
        if not math.isfinite(float(self.fee_per_unit)) or self.fee_per_unit < 0:
            raise ValueError("fee_per_unit must be finite and non-negative")
        if not isinstance(self.allow_partial_fills, bool):
            raise ValueError("allow_partial_fills must be boolean")


class ExecutionSimulator:
    """Execution model supporting point-in-time depth and conservative queueing."""
    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()

    @staticmethod
    def advance_queue_ahead(queue_ahead_quantity: int, evidence: QueueEvidence) -> int:
        if not isinstance(queue_ahead_quantity, int) or isinstance(queue_ahead_quantity, bool) or queue_ahead_quantity < 0:
            raise ValueError("queue_ahead_quantity must be a non-negative integer")
        consumed = evidence.executed_quantity + evidence.cancelled_quantity_ahead
        return max(0, queue_ahead_quantity - consumed)

    @staticmethod
    def _stop_triggered(order: SimOrder, market_price: float) -> bool:
        if order.order_type != OrderType.STOP:
            return True
        if order.stop_price is None:
            raise ValueError("stop_price is required for STOP orders")
        return market_price >= order.stop_price if order.side == ExecutionSide.BUY else market_price <= order.stop_price

    def _slippage_price(self, side: ExecutionSide, market_price: float) -> float:
        direction = 1 if side == ExecutionSide.BUY else -1
        price = market_price * (1 + direction * self.config.slippage_bps / 10_000)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("slippage-adjusted fill price must be finite and positive")
        return price

    @staticmethod
    def _timestamp(timestamp_ns: object, name: str) -> int:
        return _strict_nonnegative_int(timestamp_ns, name)

    def execute(self, order: SimOrder, market_price: float, timestamp_ns: int) -> SimFill:
        timestamp_ns = self._timestamp(timestamp_ns, "timestamp_ns")
        if not math.isfinite(float(market_price)) or market_price <= 0:
            raise ValueError("market_price must be finite and greater than zero")
        if timestamp_ns < order.submitted_at_ns:
            raise ValueError("fill timestamp cannot precede order submission")
        if not self._stop_triggered(order, market_price):
            raise ValueError("stop order has not triggered")
        fill_time = timestamp_ns + self.config.latency_ns
        return SimFill(order.order_id, order.instrument, order.side, order.quantity,
                       self._slippage_price(order.side, market_price), fill_time,
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

    def _slipped_level_price(self, order: SimOrder, level: DepthLevel) -> float | None:
        price = self._slippage_price(order.side, level.price)
        if order.order_type != OrderType.LIMIT:
            return price
        if order.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        if order.side == ExecutionSide.BUY and price > order.limit_price:
            return None
        if order.side == ExecutionSide.SELL and price < order.limit_price:
            return None
        return price

    def _simulate_depth(self, order: SimOrder, book: OrderBook, timestamp_ns: int,
                        queue_evidence: Iterable[QueueEvidence] = ()) -> ExecutionResult:
        levels = self._executable_levels(order, book)
        if not levels:
            return ExecutionResult((), order.quantity, True, "no executable depth")
        queue_ahead = order.queue_ahead_quantity
        evidence_by_price: dict[float, int] = {}
        for evidence in queue_evidence:
            evidence_by_price[evidence.price] = evidence_by_price.get(evidence.price, 0) + evidence.executed_quantity + evidence.cancelled_quantity_ahead
        best_price = levels[0].price
        if queue_ahead > 0:
            queue_ahead = max(0, queue_ahead - evidence_by_price.get(best_price, 0))
            if queue_ahead > 0:
                return ExecutionResult((), order.quantity, True, "queue ahead not depleted")
        executable = sum(level.quantity for level in levels)
        if order.time_in_force == TimeInForce.FOK and executable < order.quantity:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth for FOK")
        if not self.config.allow_partial_fills and executable < order.quantity:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth")
        remaining = order.quantity
        fills: list[SimFill] = []
        for level in levels:
            if remaining <= 0:
                break
            if level.quantity <= 0:
                continue
            slipped_price = self._slipped_level_price(order, level)
            if slipped_price is None:
                break
            take = min(remaining, level.quantity)
            fills.append(SimFill(order.order_id, order.instrument, order.side, take,
                                 slipped_price, timestamp_ns + self.config.latency_ns,
                                 take * self.config.fee_per_unit))
            remaining -= take
        if not fills:
            return ExecutionResult((), order.quantity, True, "no executable quantity")
        if order.time_in_force == TimeInForce.FOK and remaining:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth for FOK")
        return ExecutionResult(tuple(fills), remaining, False, None if remaining == 0 else "partial fill")

    def execute_depth(self, order: SimOrder, book: OrderBook, timestamp_ns: int,
                      queue_evidence: Iterable[QueueEvidence] = ()) -> ExecutionResult:
        timestamp_ns = self._timestamp(timestamp_ns, "timestamp_ns")
        if order.order_type == OrderType.STOP:
            best = book.asks if order.side == ExecutionSide.BUY else book.bids
            if not best or not self._stop_triggered(order, best[0].price):
                return ExecutionResult((), order.quantity, True, "stop order has not triggered")
        result = self._simulate_depth(order, book, timestamp_ns, queue_evidence)
        if order.time_in_force == TimeInForce.IOC and result.fills and result.remaining_quantity > 0:
            return ExecutionResult(result.fills, 0, False, "IOC remainder cancelled")
        return result

    def execute_depth_updates(self, order: SimOrder,
                              updates: Iterable[tuple[int, OrderBook, Iterable[QueueEvidence]]]) -> ExecutionResult:
        remaining = order.quantity
        queue_ahead = order.queue_ahead_quantity
        consumed_by_price: dict[float, int] = {}
        fills: list[SimFill] = []
        previous_timestamp_ns: int | None = None
        for timestamp_ns, book, evidence in updates:
            timestamp_ns = self._timestamp(timestamp_ns, "timestamp_ns")
            if previous_timestamp_ns is not None and timestamp_ns <= previous_timestamp_ns:
                raise ValueError("depth updates must be strictly ordered by timestamp_ns")
            previous_timestamp_ns = timestamp_ns
            if remaining <= 0:
                break
            levels = self._executable_levels(order, book)
            if order.order_type == OrderType.STOP:
                best = book.asks if order.side == ExecutionSide.BUY else book.bids
                if not best or not self._stop_triggered(order, best[0].price):
                    continue
            if not levels:
                continue
            evidence_by_price: dict[float, int] = {}
            for item in evidence:
                evidence_by_price[item.price] = evidence_by_price.get(item.price, 0) + item.executed_quantity + item.cancelled_quantity_ahead
            if queue_ahead > 0:
                queue_ahead = max(0, queue_ahead - evidence_by_price.get(levels[0].price, 0))
                if queue_ahead > 0:
                    continue
            for level in levels:
                already_consumed = consumed_by_price.get(level.price, 0)
                newly_available = max(0, level.quantity - already_consumed)
                if newly_available <= 0:
                    continue
                slipped_price = self._slipped_level_price(order, level)
                if slipped_price is None:
                    break
                take = min(remaining, newly_available)
                fills.append(SimFill(order.order_id, order.instrument, order.side, take,
                                     slipped_price, timestamp_ns + self.config.latency_ns,
                                     take * self.config.fee_per_unit))
                consumed_by_price[level.price] = already_consumed + take
                remaining -= take
                if remaining <= 0:
                    break
        if not fills:
            return ExecutionResult((), order.quantity, True, "no executable depth")
        if order.time_in_force == TimeInForce.FOK and remaining:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth for FOK")
        if not self.config.allow_partial_fills and remaining:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth across updates")
        if order.time_in_force == TimeInForce.IOC and remaining:
            return ExecutionResult(tuple(fills), 0, False, "IOC remainder cancelled")
        return ExecutionResult(tuple(fills), remaining, False, None if remaining == 0 else "partial fill")

    def execute_many(self, orders: Iterable[tuple[SimOrder, float, int]]) -> tuple[SimFill, ...]:
        return tuple(self.execute(order, price, timestamp_ns) for order, price, timestamp_ns in orders)

    def execute_many_atomic(self, legs: Iterable[tuple[SimOrder, OrderBook, int]]) -> AtomicExecutionResult:
        legs = tuple(legs)
        leg_results = tuple(self.execute_depth(order, book, timestamp_ns) for order, book, timestamp_ns in legs)
        if not leg_results:
            return AtomicExecutionResult((), (), True, "atomic transaction has no legs")
        if any(result.rejected or result.remaining_quantity != 0 or
               (order.time_in_force == TimeInForce.IOC and sum(fill.quantity for fill in result.fills) < order.quantity)
               for (order, _, _), result in zip(legs, leg_results)):
            return AtomicExecutionResult((), leg_results, True, "atomic rollback: one or more legs did not fully execute")
        fills = tuple(fill for result in leg_results for fill in result.fills)
        return AtomicExecutionResult(fills, leg_results, False, None)
