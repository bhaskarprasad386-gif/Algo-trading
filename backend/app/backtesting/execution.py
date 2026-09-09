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


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


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
        if self.quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        if self.submitted_at_ns < 0:
            raise ValueError("submitted_at_ns cannot be negative")
        if self.queue_ahead_quantity < 0:
            raise ValueError("queue_ahead_quantity cannot be negative")
        if not isinstance(self.time_in_force, TimeInForce):
            raise ValueError("invalid time_in_force")
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
class QueueEvidence:
    """Observed events that can legitimately advance a resting queue position.

    A disappearing depth level is not treated as a fill: the caller must supply
    explicit executed or cancelled quantity supported by source data.
    """

    price: float
    executed_quantity: int = 0
    cancelled_quantity_ahead: int = 0

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("queue evidence price must be positive")
        if self.executed_quantity < 0 or self.cancelled_quantity_ahead < 0:
            raise ValueError("queue evidence quantities cannot be negative")


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
class AtomicExecutionResult:
    """All-or-nothing result for a multi-leg execution attempt.

    Leg results retain diagnostics, while ``fills`` is empty whenever any leg
    fails to fully execute. The simulator has no external state mutation, so
    rollback is represented by withholding all tentative fills from commit.
    """

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
        if self.slippage_bps < 0 or self.latency_ns < 0 or self.fee_per_unit < 0:
            raise ValueError("execution costs and latency cannot be negative")


class ExecutionSimulator:
    """Execution model supporting point-in-time depth and conservative queueing."""

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()

    @staticmethod
    def advance_queue_ahead(queue_ahead_quantity: int, evidence: QueueEvidence) -> int:
        """Advance a resting queue only from explicit source-backed evidence."""
        if queue_ahead_quantity < 0:
            raise ValueError("queue_ahead_quantity cannot be negative")
        consumed = evidence.executed_quantity + evidence.cancelled_quantity_ahead
        return max(0, queue_ahead_quantity - consumed)

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

    def execute_depth(
        self,
        order: SimOrder,
        book: OrderBook,
        timestamp_ns: int,
        queue_evidence: Iterable[QueueEvidence] = (),
    ) -> ExecutionResult:
        """Consume displayed depth after applying explicit queue evidence.

        Queue advancement is price-specific and can combine executed quantity
        with cancellations ahead. A book disappearing or shrinking by itself
        never creates a fill.
        """
        if timestamp_ns < order.submitted_at_ns:
            raise ValueError("fill timestamp cannot precede order submission")
        levels = self._executable_levels(order, book)
        if not levels:
            return ExecutionResult((), order.quantity, True, "no executable depth")

        queue_ahead = order.queue_ahead_quantity
        evidence_by_price: dict[float, int] = {}
        for evidence in queue_evidence:
            evidence_by_price[evidence.price] = evidence_by_price.get(evidence.price, 0) + (
                evidence.executed_quantity + evidence.cancelled_quantity_ahead
            )

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
            fills.append(SimFill(order.order_id, order.instrument, order.side, take, level.price,
                                 timestamp_ns + self.config.latency_ns, take * self.config.fee_per_unit))
            remaining -= take

        if not fills:
            return ExecutionResult((), order.quantity, True, "no executable quantity")
        return ExecutionResult(tuple(fills), remaining, False, None if remaining == 0 else "partial fill")

    def execute_depth_updates(
        self,
        order: SimOrder,
        updates: Iterable[tuple[int, OrderBook, Iterable[QueueEvidence]]],
    ) -> ExecutionResult:
        """Replay timestamped book updates without reusing stale displayed depth.

        Each level's quantity is treated as currently displayed liquidity. Across
        updates, only quantity newly displayed above the simulator's consumed
        amount is executable; unchanged depth cannot be filled twice. Resting
        queue-ahead is reduced only by explicit evidence at the order price.
        """
        remaining = order.quantity
        queue_ahead = order.queue_ahead_quantity
        consumed_by_price: dict[float, int] = {}
        fills: list[SimFill] = []

        for timestamp_ns, book, evidence in updates:
            if remaining <= 0:
                break
            if timestamp_ns < order.submitted_at_ns:
                raise ValueError("fill timestamp cannot precede order submission")
            levels = self._executable_levels(order, book)
            if not levels:
                continue

            evidence_by_price: dict[float, int] = {}
            for item in evidence:
                evidence_by_price[item.price] = evidence_by_price.get(item.price, 0) + (
                    item.executed_quantity + item.cancelled_quantity_ahead
                )
            if queue_ahead > 0:
                queue_ahead = max(0, queue_ahead - evidence_by_price.get(levels[0].price, 0))
                if queue_ahead > 0:
                    continue

            for level in levels:
                already_consumed = consumed_by_price.get(level.price, 0)
                newly_available = max(0, level.quantity - already_consumed)
                if newly_available <= 0:
                    continue
                take = min(remaining, newly_available)
                fills.append(SimFill(order.order_id, order.instrument, order.side, take, level.price,
                                     timestamp_ns + self.config.latency_ns, take * self.config.fee_per_unit))
                consumed_by_price[level.price] = already_consumed + take
                remaining -= take
                if remaining <= 0:
                    break

        if not fills:
            return ExecutionResult((), order.quantity, True, "no executable depth")
        if order.time_in_force == TimeInForce.FOK and remaining:
            return ExecutionResult((), order.quantity, True, "insufficient displayed depth for FOK")
        return ExecutionResult(tuple(fills), remaining, False, None if remaining == 0 else "partial fill")

    def execute_many(self, orders: Iterable[tuple[SimOrder, float, int]]) -> tuple[SimFill, ...]:
        return tuple(self.execute(order, price, timestamp_ns) for order, price, timestamp_ns in orders)

    def execute_many_atomic(
        self,
        legs: Iterable[tuple[SimOrder, OrderBook, int]],
    ) -> AtomicExecutionResult:
        """Execute multi-leg orders transactionally: commit only full-leg success.

        Each leg is simulated independently first. If every leg fills completely,
        all fills are returned as the committed transaction. If any leg rejects
        or partially fills, every tentative fill is discarded and the result is
        marked rejected, preventing a backtest from inventing an unhedged leg.
        """
        leg_results = tuple(
            self.execute_depth(order, book, timestamp_ns)
            for order, book, timestamp_ns in legs
        )
        if not leg_results:
            return AtomicExecutionResult((), (), True, "atomic transaction has no legs")
        if any(result.rejected or result.remaining_quantity != 0 for result in leg_results):
            return AtomicExecutionResult(
                (),
                leg_results,
                True,
                "atomic rollback: one or more legs did not fully execute",
            )
        fills = tuple(fill for result in leg_results for fill in result.fills)
        return AtomicExecutionResult(fills, leg_results, False, None)
