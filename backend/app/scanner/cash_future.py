"""Cash-vs-futures arbitrage calculations.

Pure calculation layer: broker/API adapters and UI stay outside this module so
scanner rules can be customized without rewriting execution/data plumbing.
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
    min_days_to_expiry: int = 0
    max_days_to_expiry: Optional[int] = None
    charges: float = 0.0
    funding_cost: float = 0.0
    history_days: int = 365
    gap_match_tolerance: float = 0.0
    graph_days: int = 30
    universe: str = "NIFTY_50"


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
    if bid <= 0 or ask <= 0 or ltp <= 0:
        return None
    return (ask - bid) / ltp * 100.0


def calculate_cash_future(cash: CashQuote, future: FutureQuote, config: CashFutureConfig) -> CashFutureResult:
    """Calculate executable cash-future spread after configurable costs/checks."""
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
    gross = gap * future.lot_size
    net = gross - config.charges - config.funding_cost
    deployed = cash.ltp * future.lot_size + future.margin_required
    roi = (net / deployed * 100.0) if deployed > 0 else 0.0

    reasons: list[str] = []
    if gap < config.min_gap:
        reasons.append("gap_below_minimum")
    if gap_pct < config.min_gap_pct:
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

    spread_pct = _bid_ask_spread_pct(future.bid, future.ask, future.ltp)
    if config.max_bid_ask_spread_pct is not None and spread_pct is not None and spread_pct > config.max_bid_ask_spread_pct:
        reasons.append("bid_ask_spread_above_maximum")

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
