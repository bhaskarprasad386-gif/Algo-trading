"""Cash-vs-futures arbitrage calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
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
        for name, value in (("enabled", self.enabled), ("require_two_sided_quotes", self.require_two_sided_quotes)):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        integer_fields = {"min_volume": self.min_volume, "min_oi": self.min_oi, "min_days_to_expiry": self.min_days_to_expiry, "history_days": self.history_days, "graph_days": self.graph_days}
        if self.max_days_to_expiry is not None:
            integer_fields["max_days_to_expiry"] = self.max_days_to_expiry
        for name, value in integer_fields.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be a non-negative integer")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        non_negative = {"min_gap": self.min_gap, "min_gap_pct": self.min_gap_pct, "min_net_profit": self.min_net_profit, "min_roi_pct": self.min_roi_pct, "min_margin": self.min_margin, "charges": self.charges, "funding_cost": self.funding_cost, "gap_match_tolerance": self.gap_match_tolerance}
        if self.max_margin is not None:
            non_negative["max_margin"] = self.max_margin
        if self.max_bid_ask_spread_pct is not None:
            non_negative["max_bid_ask_spread_pct"] = self.max_bid_ask_spread_pct
        if self.max_cash_bid_ask_spread_pct is not None:
            non_negative["max_cash_bid_ask_spread_pct"] = self.max_cash_bid_ask_spread_pct
        for name, value in non_negative.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.max_margin is not None and self.max_margin < self.min_margin:
            raise ValueError("max_margin cannot be below min_margin")
        if self.max_days_to_expiry is not None and self.max_days_to_expiry < self.min_days_to_expiry:
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


def _validate_finite(name: str, value: float) -> None:
    try:
        finite = math.isfinite(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not finite:
        raise ValueError(f"{name} must be a finite number")


def _validate_positive(name: str, value: float) -> None:
    _validate_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _bid_ask_spread_pct(bid: Optional[float], ask: Optional[float], ltp: float) -> Optional[float]:
    if bid is None or ask is None:
        return None
    if not math.isfinite(bid) or not math.isfinite(ask) or not math.isfinite(ltp) or bid <= 0 or ask <= 0 or ltp <= 0 or ask < bid:
        return None
    return (ask - bid) / ltp * 100.0


def _ltp_inside_quote(bid: Optional[float], ask: Optional[float], ltp: float) -> bool:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ltp <= 0:
        return True
    if not math.isfinite(bid) or not math.isfinite(ask) or not math.isfinite(ltp) or ask < bid:
        return False
    return bid <= ltp <= ask


def _validate_result_finite(values: tuple[Optional[float], ...]) -> None:
    if any(value is not None and not math.isfinite(value) for value in values):
        raise ValueError("cash-future result must be finite")


def _validate_contract_consistency(contract_month: str, expiry: Optional[date]) -> None:
    if contract_month.upper() == "CURRENT":
        return
    try:
        parsed = datetime.strptime(contract_month, "%Y-%m")
    except ValueError as exc:
        raise ValueError("contract_month must use YYYY-MM format") from exc
    if expiry is not None and (expiry.year, expiry.month) != (parsed.year, parsed.month):
        raise ValueError("contract_month must match expiry month")


def calculate_cash_future(cash: CashQuote, future: FutureQuote, config: CashFutureConfig) -> CashFutureResult:
    if not config.enabled:
        raise ValueError("cash-future scanner is disabled")
    _validate_positive("cash ltp", cash.ltp)
    _validate_positive("future ltp", future.ltp)
    if not isinstance(future.contract_month, str) or not future.contract_month.strip():
        raise ValueError("contract_month must be a non-empty string")
    contract_month = future.contract_month.strip()
    _validate_contract_consistency(contract_month, future.expiry)
    if isinstance(future.lot_size, bool) or not isinstance(future.lot_size, int):
        raise ValueError("lot_size must be a positive integer")
    if future.lot_size <= 0:
        raise ValueError("lot_size must be greater than zero")
    _validate_finite("margin_required", future.margin_required)
    if future.margin_required < 0:
        raise ValueError("margin_required cannot be negative")
    for name, value in (("future volume", future.volume), ("future oi", future.oi)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be a non-negative integer")
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
    for name, value in (("cash bid", cash.bid), ("cash ask", cash.ask), ("future bid", future.bid), ("future ask", future.ask)):
        if value is not None:
            _validate_finite(name, value)
    gap = future.ltp - cash.ltp
    gap_pct = gap / cash.ltp * 100.0
    cash_spread_pct = _bid_ask_spread_pct(cash.bid, cash.ask, cash.ltp)
    future_spread_pct = _bid_ask_spread_pct(future.bid, future.ask, future.ltp)
    executable_gap = executable_gap_pct = cash_execution_price = future_execution_price = None
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
    _validate_result_finite((gap, gap_pct, executable_gap, executable_gap_pct, cash_execution_price, future_execution_price, cash_spread_pct, future_spread_pct, gross, config.charges, config.funding_cost, net, future.margin_required, deployed, roi))
    reasons: list[str] = []
    if cash.symbol.strip().upper() != future.symbol.strip().upper(): reasons.append("symbol_mismatch")
    threshold_gap = executable_gap if executable_gap is not None else gap
    threshold_gap_pct = executable_gap_pct if executable_gap_pct is not None else gap_pct
    if executable_gap is not None and executable_gap <= 0: reasons.append("executable_gap_non_positive")
    if threshold_gap < config.min_gap: reasons.append("gap_below_minimum")
    if threshold_gap_pct < config.min_gap_pct: reasons.append("gap_pct_below_minimum")
    if net < config.min_net_profit: reasons.append("net_profit_below_minimum")
    if roi < config.min_roi_pct: reasons.append("roi_below_minimum")
    if future.margin_required < config.min_margin: reasons.append("margin_below_minimum")
    if config.max_margin is not None and future.margin_required > config.max_margin: reasons.append("margin_above_maximum")
    if future.volume < config.min_volume: reasons.append("volume_below_minimum")
    if future.oi < config.min_oi: reasons.append("oi_below_minimum")
    if config.require_two_sided_quotes and (cash.ask is None or future.bid is None): reasons.append("missing_executable_quotes")
    if cash.bid is not None and cash.ask is not None and (cash.bid <= 0 or cash.ask <= 0 or cash.ask < cash.bid): reasons.append("invalid_cash_bid_ask")
    if future.bid is not None and future.ask is not None and (future.bid <= 0 or future.ask <= 0 or future.ask < future.bid): reasons.append("invalid_future_bid_ask")
    if cash.bid is not None and cash.bid <= 0: reasons.append("invalid_cash_bid_ask")
    if cash.ask is not None and cash.ask <= 0: reasons.append("invalid_cash_bid_ask")
    if future.bid is not None and future.bid <= 0: reasons.append("invalid_future_bid_ask")
    if future.ask is not None and future.ask <= 0: reasons.append("invalid_future_bid_ask")
    if not _ltp_inside_quote(cash.bid, cash.ask, cash.ltp): reasons.append("cash_ltp_outside_bid_ask")
    if not _ltp_inside_quote(future.bid, future.ask, future.ltp): reasons.append("future_ltp_outside_bid_ask")
    if config.max_bid_ask_spread_pct is not None:
        if future_spread_pct is not None and future_spread_pct > config.max_bid_ask_spread_pct: reasons.append("bid_ask_spread_above_maximum")
        elif future_spread_pct is None: reasons.append("future_bid_ask_unavailable")
    if config.max_cash_bid_ask_spread_pct is not None:
        if cash_spread_pct is not None and cash_spread_pct > config.max_cash_bid_ask_spread_pct: reasons.append("cash_bid_ask_spread_above_maximum")
        elif cash_spread_pct is None: reasons.append("cash_bid_ask_unavailable")
    if future.expiry is not None:
        days = (future.expiry - date.today()).days
        if days < config.min_days_to_expiry: reasons.append("days_to_expiry_below_minimum")
        if config.max_days_to_expiry is not None and days > config.max_days_to_expiry: reasons.append("days_to_expiry_above_maximum")
    reasons = list(dict.fromkeys(reasons))
    return CashFutureResult(symbol=cash.symbol.upper(), contract_month=contract_month, cash_ltp=cash.ltp, future_ltp=future.ltp, gap=gap, gap_pct=gap_pct, executable_gap=executable_gap, executable_gap_pct=executable_gap_pct, cash_execution_price=cash_execution_price, future_execution_price=future_execution_price, cash_bid_ask_spread_pct=cash_spread_pct, future_bid_ask_spread_pct=future_spread_pct, lot_size=future.lot_size, gross_spread_profit=gross, charges=config.charges, funding_cost=config.funding_cost, net_profit=net, margin_required=future.margin_required, deployed_capital=deployed, roi_pct=roi, executable=not reasons, rejection_reasons=tuple(reasons))
