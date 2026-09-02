"""Deterministic pre-trade margin, risk and price-circuit checks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PreTradeLimits:
    max_risk_amount: float
    min_available_margin: float = 0.0

    def __post_init__(self) -> None:
        if self.max_risk_amount < 0 or self.min_available_margin < 0:
            raise ValueError("risk and margin limits cannot be negative")


@dataclass(frozen=True)
class PreTradeOrder:
    price: float
    quantity: float
    stop_loss: float
    required_margin: float
    available_margin: float
    lower_circuit: float | None = None
    upper_circuit: float | None = None


@dataclass(frozen=True)
class PreTradeResult:
    approved: bool
    reasons: tuple[str, ...] = ()


def check_pre_trade(order: PreTradeOrder, limits: PreTradeLimits) -> PreTradeResult:
    reasons: list[str] = []
    if order.price <= 0 or order.quantity <= 0:
        reasons.append("invalid_order_size")
    if order.stop_loss < 0:
        reasons.append("invalid_stop_loss")
    if order.required_margin > order.available_margin:
        reasons.append("insufficient_margin")
    if order.available_margin < limits.min_available_margin:
        reasons.append("margin_buffer_breached")
    risk_amount = max(order.price - order.stop_loss, 0.0) * order.quantity
    if risk_amount > limits.max_risk_amount:
        reasons.append("max_risk_exceeded")
    if order.lower_circuit is not None and order.price < order.lower_circuit:
        reasons.append("below_lower_circuit")
    if order.upper_circuit is not None and order.price > order.upper_circuit:
        reasons.append("above_upper_circuit")
    return PreTradeResult(not reasons, tuple(reasons))
