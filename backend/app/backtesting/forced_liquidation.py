"""Deterministic maintenance-margin forced liquidation for backtests."""

from dataclasses import dataclass
from typing import Mapping

from app.backtesting.execution import ExecutionSide, SimFill
from app.backtesting.portfolio import Portfolio, PortfolioSnapshot, RiskViolation


@dataclass(frozen=True)
class ForcedLiquidationResult:
    triggered: bool
    fills: tuple[SimFill, ...]
    snapshot: PortfolioSnapshot
    unresolved_instruments: tuple[str, ...] = ()
    reason: str | None = None


class ForcedLiquidationEngine:
    """Create and atomically apply only risk-reducing liquidation fills.

    Prices must come from the caller's executable market data. The engine never
    invents a liquidation price and never reverses a position.
    """

    def liquidate(
        self,
        portfolio: Portfolio,
        marks: Mapping[str, float] | None = None,
        executable_prices: Mapping[str, float] | None = None,
        timestamp_ns: int = 0,
        order_id_prefix: str = "forced-liquidation",
    ) -> ForcedLiquidationResult:
        if timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if not order_id_prefix.strip():
            raise ValueError("order_id_prefix is required")

        marks = dict(marks or {})
        prices = dict(executable_prices or {})
        before = portfolio.snapshot(marks)
        if before.equity + 1e-9 >= before.maintenance_margin:
            return ForcedLiquidationResult(False, (), before, (), "maintenance margin is healthy")

        positions = sorted(before.positions, key=lambda p: (-abs(p.quantity * marks.get(p.instrument, p.average_price)), p.instrument))
        unresolved: list[str] = []
        fills: list[SimFill] = []
        for position in positions:
            price = prices.get(position.instrument)
            if price is None:
                unresolved.append(position.instrument)
                continue
            if price <= 0:
                raise ValueError(f"executable liquidation price must be positive for {position.instrument}")
            side = ExecutionSide.SELL if position.quantity > 0 else ExecutionSide.BUY
            fills.append(
                SimFill(
                    order_id=f"{order_id_prefix}:{position.instrument}",
                    instrument=position.instrument,
                    side=side,
                    quantity=abs(position.quantity),
                    price=float(price),
                    filled_at_ns=timestamp_ns,
                )
            )

        if not fills:
            return ForcedLiquidationResult(
                True, (), before, tuple(unresolved), "maintenance breach unresolved: no executable liquidation price"
            )

        try:
            after = portfolio.apply_fills_atomic(tuple(fills), marks)
        except Exception as exc:
            raise RiskViolation(f"forced liquidation batch rejected atomically: {exc}") from exc

        still_breached = after.equity + 1e-9 < after.maintenance_margin
        reason = None
        if still_breached:
            remaining = tuple(p.instrument for p in after.positions)
            unresolved = sorted(set(unresolved).union(remaining))
            reason = "maintenance breach remains after executable liquidation; exposure retained"
        elif unresolved:
            reason = "maintenance restored; instruments without executable prices remain untouched"
        else:
            reason = "maintenance margin restored"

        return ForcedLiquidationResult(True, tuple(fills), after, tuple(unresolved), reason)
