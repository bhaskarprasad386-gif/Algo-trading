"""Atomic execution bridge for generic multi-leg backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.backtesting.execution import ExecutionResult, ExecutionSimulator, ExecutionSide, SimFill, SimOrder
from app.backtesting.multi_leg import MultiLegSignal


@dataclass(frozen=True)
class MultiLegExecutionResult:
    """All-or-none basket result backed by the shared execution simulator."""

    signal_id: str
    fills: tuple[SimFill, ...]
    rejected: bool = False
    reason: str | None = None


class AtomicMultiLegExecutor:
    """Execute every strategy leg only when the complete basket is executable."""

    def __init__(self, simulator: ExecutionSimulator | None = None) -> None:
        self.simulator = simulator or ExecutionSimulator()

    def execute(
        self,
        signal: MultiLegSignal,
        prices: Mapping[str, float],
    ) -> MultiLegExecutionResult:
        missing = [leg.instrument for leg in signal.legs if leg.instrument not in prices]
        if missing:
            return MultiLegExecutionResult(signal.signal_id, (), True, f"missing market data: {', '.join(missing)}")

        results: list[ExecutionResult] = []
        for leg in signal.legs:
            order = SimOrder(
                order_id=f"{signal.signal_id}:{leg.leg_id}",
                instrument=leg.instrument,
                side=ExecutionSide(leg.side.value),
                quantity=leg.quantity,
                submitted_at_ns=signal.timestamp_ns,
            )
            result = self.simulator.execute_depth(
                order,
                book=_single_level_book(leg.side.value, float(prices[leg.instrument]), leg.quantity),
                timestamp_ns=signal.timestamp_ns,
            )
            if result.rejected or result.remaining_quantity:
                return MultiLegExecutionResult(signal.signal_id, (), True, f"leg {leg.leg_id} not fully executable")
            results.append(result)

        fills = tuple(fill for result in results for fill in result.fills)
        return MultiLegExecutionResult(signal.signal_id, fills)


def _single_level_book(side: str, price: float, quantity: int):
    from app.backtesting.execution import DepthLevel, OrderBook

    level = DepthLevel(price, quantity)
    return OrderBook(asks=(level,)) if side == "BUY" else OrderBook(bids=(level,))
