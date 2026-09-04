"""Cash-vs-futures arbitrage calculations.

Pure calculation layer: broker/API adapters and UI stay outside this module so
scanner rules can be customized without rewriting data/execution plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class CashFutureConfig:
    enabled: bool = True
    min_gap: float = 0.0
    min_gap_pct: float = 0.0
    min_net_profit: float = 0.0
    min_roi_pct: float = 0.0
    min_margin: float = 0.0
    max_margin: Optional[float] = None
    min_volume: int = 0
    min_oi: int = 0
    max_bid_ask_spread_pct: Optional[float] = None
    max_cash_bid_ask_spread_pct: Optional[float] = None
    require_two_sided_quotes: bool = True
    min_days_to_expiry: int = 0
    max_days_to_expiry: Optional[int] = None
    charges: float = 0.0
    funding_cost: float = 0.0
    history_days: int = 365
    gap_match_tolerance: float = 0.0
    graph_days: int = 30
    universe: str = "NIFTY_50"

    def __post_init__(self) -> None:
        non_negative = {
            "min_gap": self.min_gap,
            "min_gap_pct": self.min_gap_pct,
            "min_net_profit": self.min_net_profit,
            "min_roi_pct": self.min_roi_pct,
            "min_margin": self.min_margin,
            "min_volume": self.min_volume,
            "min_oi": self.min_oi,
            "min_days_to_expiry": self.min_days_to_expiry,
            "charges": self.charges,
            "funding_cost": self.funding_cost,
            "history_days": self.history_days,
            "gap_match_tolerance": self.gap_match_tolerance,
            "graph_days": self.graph_days,
        }
        if self.max_margin is not None:
            non_negative["max_margin"] = self.max_margin
        if self.max_days_to_expiry is not None:
            non_negative["max_days_to_expiry"] = self.max_days_to_expiry
        if self.max_bid_ask_spread_pct is not None:
            non_negative["max_bid_ask_spread_pct"] = self.max_bid_ask_spread_pct
        if self.max_cash_bid_ask_spread_pct is not None:
            non_negative["max_cash_bid_ask_spread_pct"] = self.max_cash_bid_ask_spread_pct

        for name, value in non_negative.items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative")

        if self.max_margin is not None and self.max_margin < self.min_margin:
            raise ValueError("max_margin cannot be below min_margin")
        if (
            self.max_days_to_expiry is not None
            and self.max_days_to_expiry < self.min_days_to_expiry
        ):
            raise ValueError("max_days_to_expiry cannot be below min_days_to_expiry")


@dataclass(frozen=True)
class FutureQuote:
    symbol: str
    contract_month: str
    ltp: float
    lot_size: int
    margin_required: float
    volume: int = 0
    oi: int = 0
    bid: Optional[float] = None
    ask: Optional[float] = None
    expiry: Optional[date] = None


@dataclass(frozen=True)
class CashQuote:
    symbol: str
    ltp: float
    bid: Optional[float] = None
    ask: Optional[float] = None


@dataclass(frozen=True)
class CashFutureResult:
    symbol: str
    contract_month: str
    cash_ltp: float
    future_ltp: float
    gap: float
    gap_pct: float
    executable_gap: Optional[float]
    executable_gap_pct: Optional[float]
    cash_execution_price: Optional[float]
    future_execution_price: Optional[float]
    cash_bid_ask_spread_pct: Optional[float]
    future_bid_ask_spread_pct: Optional[float]
    lot_size: int
    gross_spread_profit: float
    charges: float
    funding_cost: float
    net_profit: float
    margin_required: float
    deployed_capital: float
    roi_pct: float
    executable: bool
    rejection_reasons: tuple[str, ...] = field(default_factory=tuple)


def _validate_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _bid_ask_spread_pct(bid: Optional[float], ask: Optional[float], ltp: float) -> Optional[float]:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ltp <= 0 or ask < bid:
        return None
    return (ask - bid) / ltp * 100.0


def calculate_cash_future(cash: CashQuote, future: FutureQuote, config: CashFutureConfig) -> CashFutureResult:
    """Calculate a cash-future opportunity using executable bid/ask prices."""
    if not config.enabled:
        raise ValueError("cash-future scanner is disabled")
    _validate_positive("cash ltp", cash.ltp)
    _validate_positive("future ltp", future.ltp)
    if future.lot_size <= 0:
        raise ValueError("lot_size must be greater than zero")
    if future.margin_required < 0:
        raise ValueError("margin_required cannot be negative")

    gap = future.ltp - cash.ltp
    gap_pct = gap / cash.ltp * 100.0
    cash_spread_pct = _bid_ask_spread_pct(cash.bid, cash.ask, cash.ltp)
    future_spread_pct = _bid_ask_spread_pct(future.bid, future.ask, future.ltp)

    executable_gap: Optional[float] = None
    executable_gap_pct: Optional[float] = None
    cash_execution_price: Optional[float] = None
    future_execution_price: Optional[float] = None
    if cash.ask is not None and future.bid is not None and cash.ask > 0 and future.bid > 0:
        cash_execution_price = cash.ask
        future_execution_price = future.bid
        executable_gap = future.bid - cash.ask
        executable_gap_pct = executable_gap / cash.ask * 100.0

    profit_gap = executable_gap if executable_gap is not None else gap
    gross = round(profit_gap * future.lot_size, 2)
    net = round(gross - config.charges - config.funding_cost, 2)
    cash_capital_price = cash_execution_price if cash_execution_price is not None else cash.ltp
    deployed = round(cash_capital_price * future.lot_size + future.margin_required, 2)
    roi = round((net / deployed * 100.0) if deployed > 0 else 0.0, 6)

    reasons: list[str] = []
    threshold_gap = executable_gap if executable_gap is not None else gap
    threshold_gap_pct = executable_gap_pct if executable_gap_pct is not None else gap_pct
    if executable_gap is not None and executable_gap <= 0:
        reasons.append("executable_gap_non_positive")
    if threshold_gap < config.min_gap:
        reasons.append("gap_below_minimum")
    if threshold_gap_pct < config.min_gap_pct:
        reasons.append("gap_pct_below_minimum")
    if net < config.min_net_profit:
        reasons.append("net_profit_below_minimum")
    if roi < config.min_roi_pct:
        reasons.append("roi_below_minimum")
    if future.margin_required < config.min_margin:
        reasons.append("margin_below_minimum")
    if config.max_margin is not None and future.margin_required > config.max_margin:
        reasons.append("margin_above_maximum")
    if future.volume < config.min_volume:
        reasons.append("volume_below_minimum")
    if future.oi < config.min_oi:
        reasons.append("oi_below_minimum")
    if config.require_two_sided_quotes and (cash.ask is None or future.bid is None):
        reasons.append("missing_executable_quotes")

    if cash.bid is not None and cash.ask is not None:
        if cash.bid <= 0 or cash.ask <= 0:
            reasons.append("invalid_cash_bid_ask")
        elif cash.ask < cash.bid:
            reasons.append("invalid_cash_bid_ask")
    if future.bid is not None and future.ask is not None:
        if future.bid <= 0 or future.ask <= 0:
            reasons.append("invalid_future_bid_ask")
        elif future.ask < future.bid:
            reasons.append("invalid_future_bid_ask")

    if cash.bid is not None and cash.bid <= 0:
        reasons.append("invalid_cash_bid_ask")
    if cash.ask is not None and cash.ask <= 0:
        reasons.append("invalid_cash_bid_ask")
    if future.bid is not None and future.bid <= 0:
        reasons.append("invalid_future_bid_ask")
    if future.ask is not None and future.ask <= 0:
        reasons.append("invalid_future_bid_ask")

    reasons = list(dict.fromkeys(reasons))

    if config.max_bid_ask_spread_pct is not None:
        if future_spread_pct is not None and future_spread_pct > config.max_bid_ask_spread_pct:
            reasons.append("bid_ask_spread_above_maximum")
        elif future_spread_pct is None:
            reasons.append("future_bid_ask_unavailable")
    if config.max_cash_bid_ask_spread_pct is not None:
        if cash_spread_pct is not None and cash_spread_pct > config.max_cash_bid_ask_spread_pct:
            reasons.append("cash_bid_ask_spread_above_maximum")
        elif cash_spread_pct is None:
            reasons.append("cash_bid_ask_unavailable")
    if future.expiry is not None:
        days = (future.expiry - date.today()).days
        if days < config.min_days_to_expiry:
            reasons.append("days_to_expiry_below_minimum")
        if config.max_days_to_expiry is not None and days > config.max_days_to_expiry:
            reasons.append("days_to_expiry_above_maximum")

    return CashFutureResult(
        symbol=cash.symbol.upper(),
        contract_month=future.contract_month,
        cash_ltp=cash.ltp,
        future_ltp=future.ltp,
        gap=gap,
        gap_pct=gap_pct,
        executable_gap=executable_gap,
        executable_gap_pct=executable_gap_pct,
        cash_execution_price=cash_execution_price,
        future_execution_price=future_execution_price,
        cash_bid_ask_spread_pct=cash_spread_pct,
        future_bid_ask_spread_pct=future_spread_pct,
        lot_size=future.lot_size,
        gross_spread_profit=gross,
        charges=config.charges,
        funding_cost=config.funding_cost,
        net_profit=net,
        margin_required=future.margin_required,
        deployed_capital=deployed,
        roi_pct=roi,
        executable=not reasons,
        rejection_reasons=tuple(reasons),
    )
