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


def _finite_positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def _finite_nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


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
        if not isinstance(self.order_id, str) or not self.order_id.strip() or not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("order_id and instrument are required")
        if not isinstance(self.side, ExecutionSide) or not isinstance(self.order_type, OrderType):
            raise ValueError("invalid order side or order type")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if isinstance(self.submitted_at_ns, bool) or not isinstance(self.submitted_at_ns, int) or self.submitted_at_ns < 0:
            raise ValueError("submitted_at_ns must be a non-negative integer")
        if not isinstance(self.queue_ahead_quantity, int) or isinstance(self.queue_ahead_quantity, bool) or self.queue_ahead_quantity < 0:
            raise ValueError("queue_ahead_quantity must be a non-negative integer")
        if not isinstance(self.time_in_force, TimeInForce):
            raise ValueError("invalid time_in_force")
        if self.order_type == OrderType.LIMIT:
            _finite_positive(self.limit_price, "limit_price")
        elif self.limit_price is not None:
            raise ValueError("limit_price is only valid for LIMIT orders")
        if self.order_type == OrderType.STOP:
            _finite_positive(self.stop_price, "stop_price")
        elif self.stop_price is not None:
            raise ValueError("stop_price is only valid for STOP orders")


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: int

    def __post_init__(self) -> None:
        _finite_positive(self.price, "depth price")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity < 0:
            raise ValueError("depth quantity must be a non-negative integer")


@dataclass(frozen=True)
class OrderBook:
    """Point-in-time executable order-book snapshot, best level first."""
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.bids, tuple) or not isinstance(self.asks, tuple):
            raise TypeError("order-book bids and asks must be tuples")
        if any(not isinstance(level, DepthLevel) for level in self.bids + self.asks):
            raise TypeError("order-book levels must be DepthLevel instances")
        bid_prices = tuple(x.price for x in self.bids)
        ask_prices = tuple(x.price for x in self.asks)
        if len(bid_prices) != len(set(bid_prices)):
            raise ValueError("bids cannot contain duplicate price levels")
        if len(ask_prices) != len(set(ask_prices)):
            raise ValueError("asks cannot contain duplicate price levels")
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
        _finite_positive(self.price, "queue evidence price")
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
        if not isinstance(self.order_id, str) or not self.order_id.strip() or not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("fill order_id and instrument are required")
        if not isinstance(self.side, ExecutionSide):
            raise ValueError("invalid fill side")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise ValueError("fill quantity must be a positive integer")
        _finite_positive(self.price, "fill price")
        if isinstance(self.filled_at_ns, bool) or not isinstance(self.filled_at_ns, int) or self.filled_at_ns < 0:
            raise ValueError("fill timestamp must be a non-negative integer")
        _finite_nonnegative(self.fee, "fill fee")


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
        _finite_nonnegative(self.slippage_bps, "slippage_bps")
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps must be below 10000")
        if isinstance(self.latency_ns, bool) or not isinstance(self.latency_ns, int) or self.latency_ns < 0:
            raise ValueError("latency_ns must be a non-negative integer")
        _finite_nonnegative(self.fee_per_unit, "fee_per_unit")
        if not isinstance(self.allow_partial_fills, bool):
            raise TypeError("allow_partial_fills must be boolean")


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
        _finite_positive(market_price, "market_price")
        return market_price >= order.stop_price if order.side == ExecutionSide.BUY else market_price <= order.stop_price

    def _slippage_price(self, side: ExecutionSide, market_price: float) -> float:
        _finite_positive(market_price, "market_price")
        direction = 1 if side == ExecutionSide.BUY else -1
        price = market_price * (1 + direction * self.config.slippage_bps / 10_000)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("slippage-adjusted fill price must be finite and positive")
        return price

    def execute(self, order: SimOrder, market_price: float, timestamp_ns: int) -> SimFill:
        _finite_positive(market_price, "market_price")
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < order.submitted_at_ns:
            raise ValueError("fill timestamp must be an integer and cannot precede order submission")
        if not self._stop_triggered(order, market_price):
            raise ValueError("stop order has not triggered")
        fill_time = timestamp_ns + self.config.latency_ns
        return SimFill(order.order_id, order.instrument, order.side, order.quantity,
                       self._slippage_price(order.side, market_price), fill_time,
                       order.quantity * self.config.fee_per_unit)

    @staticmethod
    def _executable_levels(order: SimOrder, book: OrderBook) -> tuple[DepthLevel, ...]:
        levels = book.asks if order.side == ExecutionSide.BUY else book.bids
        if order.order_type == OrderType.STOP or order.order_type != OrderType.LIMIT:
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

    def execute_depth(self, order: SimOrder, book: OrderBook, timestamp_ns: int, queue_evidence: Iterable[QueueEvidence] = ()) -> ExecutionResult:
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < order.submitted_at_ns:
            raise ValueError("fill timestamp must be an integer and cannot precede order submission")
        if order.order_type == OrderType.STOP:
            best = book.asks if order.side == ExecutionSide.BUY else book.bids
            if not best or not self._stop_triggered(order, best[0].price):
                return ExecutionResult((), order.quantity, True, "stop order has not triggered")
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
            take = min(remaining, level.quantity)
            fills.append(SimFill(order.order_id, order.instrument, order.side, take,
                                 self._slippage_price(order.side, level.price),
                                 timestamp_ns + self.config.latency_ns, take * self.config.fee_per_unit))
            remaining -= take
        if not fills:
            return ExecutionResult((), order.quantity, True, "no executable quantity")
        if order.time_in_force == TimeInForce.FOK and remaining:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth for FOK")
        return ExecutionResult(tuple(fills), remaining, False, None if remaining == 0 else "partial fill")

    def execute_depth_updates(self, order: SimOrder, updates: Iterable[tuple[int, OrderBook, Iterable[QueueEvidence]]]) -> ExecutionResult:
        remaining = order.quantity; queue_ahead = order.queue_ahead_quantity; consumed_by_price: dict[float, int] = {}; fills: list[SimFill] = []
        previous_timestamp_ns: int | None = None
        for timestamp_ns, book, evidence in updates:
            if remaining <= 0: break
            if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < order.submitted_at_ns:
                raise ValueError("fill timestamp must be an integer and cannot precede order submission")
            if previous_timestamp_ns is not None and timestamp_ns < previous_timestamp_ns:
                raise ValueError("depth update timestamps must be monotonic non-decreasing")
            previous_timestamp_ns = timestamp_ns
            levels = self._executable_levels(order, book)
            if order.order_type == OrderType.STOP:
                best = book.asks if order.side == ExecutionSide.BUY else book.bids
                if not best or not self._stop_triggered(order, best[0].price): continue
            if not levels: continue
            evidence_by_price: dict[float, int] = {}
            for item in evidence:
                evidence_by_price[item.price] = evidence_by_price.get(item.price, 0) + item.executed_quantity + item.cancelled_quantity_ahead
            if queue_ahead > 0:
                queue_ahead = max(0, queue_ahead - evidence_by_price.get(levels[0].price, 0))
                if queue_ahead > 0: continue
            for level in levels:
                already_consumed = consumed_by_price.get(level.price, 0); newly_available = max(0, level.quantity - already_consumed)
                if newly_available <= 0: continue
                take = min(remaining, newly_available)
                fills.append(SimFill(order.order_id, order.instrument, order.side, take,
                                     self._slippage_price(order.side, level.price),
                                     timestamp_ns + self.config.latency_ns, take * self.config.fee_per_unit))
                consumed_by_price[level.price] = already_consumed + take; remaining -= take
                if remaining <= 0: break
        if not fills: return ExecutionResult((), order.quantity, True, "no executable depth")
        if order.time_in_force == TimeInForce.FOK and remaining: return ExecutionResult((), order.quantity, True, "insufficient displayed depth for FOK")
        if order.time_in_force == TimeInForce.IOC and remaining: return ExecutionResult(tuple(fills), 0, False, "IOC remainder cancelled")
        return ExecutionResult(tuple(fills), remaining, False, None if remaining == 0 else "partial fill")

    def execute_many(self, orders: Iterable[tuple[SimOrder, float, int]]) -> tuple[SimFill, ...]:
        return tuple(self.execute(order, price, timestamp_ns) for order, price, timestamp_ns in orders)

    def execute_many_atomic(self, legs: Iterable[tuple[SimOrder, OrderBook, int]]) -> AtomicExecutionResult:
        legs = tuple(legs)
        if not legs:
            return AtomicExecutionResult((), (), True, "atomic transaction has no legs")
        order_ids = tuple(order.order_id for order, _, _ in legs)
        if len(order_ids) != len(set(order_ids)):
            return AtomicExecutionResult((), (), True, "atomic transaction contains duplicate order_id")
        leg_results = tuple(self.execute_depth(order, book, timestamp_ns) for order, book, timestamp_ns in legs)
        if any(result.rejected or result.remaining_quantity != 0 or (order.time_in_force == TimeInForce.IOC and sum(fill.quantity for fill in result.fills) < order.quantity) for (order, _, _), result in zip(legs, leg_results)):
            return AtomicExecutionResult((), leg_results, True, "atomic rollback: one or more legs did not fully execute")
        fills = tuple(fill for result in leg_results for fill in result.fills)
        return AtomicExecutionResult(fills, leg_results, False, None)
