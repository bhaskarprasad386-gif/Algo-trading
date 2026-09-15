"""Executable multi-leg arbitrage primitives for liquid F&O backtests."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite
from typing import Literal

ArbitrageKind = Literal["BOX", "SYNTHETIC_CASH_CARRY", "CASH_CARRY"]


@dataclass(frozen=True)
class OptionQuote:
    timestamp_ns: int
    underlying: str
    expiry: int
    strike: float
    call_bid: float
    call_ask: float
    put_bid: float
    put_ask: float
    lot_size: int = 1
    instrument_class: Literal["STOCK", "INDEX"] = "STOCK"
    volume: int = 0
    oi: int = 0


@dataclass(frozen=True)
class FutureQuote:
    timestamp_ns: int
    underlying: str
    expiry: int
    bid: float
    ask: float
    lot_size: int = 1
    instrument_class: Literal["STOCK", "INDEX"] = "STOCK"
    volume: int = 0
    oi: int = 0


@dataclass(frozen=True)
class LiquidityPolicy:
    min_option_volume: int = 0
    min_option_oi: int = 0
    max_spread_pct: float = 100.0
    min_future_volume: int = 0
    min_future_oi: int = 0

    def __post_init__(self) -> None:
        if self.min_option_volume < 0 or self.min_option_oi < 0:
            raise ValueError("option liquidity thresholds cannot be negative")
        if self.min_future_volume < 0 or self.min_future_oi < 0:
            raise ValueError("future liquidity thresholds cannot be negative")
        if not isfinite(float(self.max_spread_pct)) or self.max_spread_pct < 0:
            raise ValueError("max_spread_pct must be finite and non-negative")

    @staticmethod
    def _accepts_quote(*, volume: int, oi: int, bid: float, ask: float,
                       min_volume: int, min_oi: int, max_spread_pct: float) -> bool:
        if volume < min_volume or oi < min_oi:
            return False
        if not all(isfinite(float(value)) for value in (bid, ask)):
            return False
        if bid < 0 or ask < bid:
            return False
        if bid == 0:
            return ask == 0
        return ((ask - bid) / bid) * 100.0 <= max_spread_pct

    def accepts(self, *, volume: int, oi: int, bid: float, ask: float) -> bool:
        return self._accepts_quote(
            volume=volume, oi=oi, bid=bid, ask=ask,
            min_volume=self.min_option_volume, min_oi=self.min_option_oi,
            max_spread_pct=self.max_spread_pct,
        )

    def accepts_option(self, option: OptionQuote) -> bool:
        quotes = (
            (option.call_bid, option.call_ask),
            (option.put_bid, option.put_ask),
        )
        return all(self.accepts(volume=option.volume, oi=option.oi, bid=bid, ask=ask)
                   for bid, ask in quotes)

    def accepts_future(self, future: FutureQuote) -> bool:
        return self._accepts_quote(
            volume=future.volume, oi=future.oi, bid=future.bid, ask=future.ask,
            min_volume=self.min_future_volume, min_oi=self.min_future_oi,
            max_spread_pct=self.max_spread_pct,
        )


@dataclass(frozen=True)
class ArbitrageOpportunity:
    kind: ArbitrageKind
    direction: Literal["LONG", "SHORT"]
    timestamp_ns: int
    underlying: str
    expiry: int
    strike_low: float | None
    strike_high: float | None
    executable_edge: float
    edge_per_lot: float
    gross_pnl: float
    width_or_notional: float


def _validate_option_quote(quote: OptionQuote) -> None:
    values = (quote.strike, quote.call_bid, quote.call_ask, quote.put_bid, quote.put_ask)
    if not all(isfinite(float(value)) for value in values):
        raise ValueError("option prices and strike must be finite")
    if quote.call_bid < 0 or quote.put_bid < 0:
        raise ValueError("option bid prices must be non-negative")
    if quote.call_ask < quote.call_bid or quote.put_ask < quote.put_bid:
        raise ValueError("option ask prices must be at least bid prices")
    if quote.lot_size <= 0:
        raise ValueError("option lot_size must be positive")
    if quote.volume < 0 or quote.oi < 0:
        raise ValueError("option volume and oi must be non-negative")


def _validate_direction(direction: str) -> None:
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")


class BoxSpreadBacktester:
    """Evaluate executable long/reverse boxes at one timestamp."""

    @staticmethod
    def evaluate(low: OptionQuote, high: OptionQuote, *,
                 direction: Literal["LONG", "SHORT"] = "LONG",
                 fees_per_unit: float = 0.0,
                 liquidity: LiquidityPolicy | None = None) -> ArbitrageOpportunity | None:
        _validate_direction(direction)
        _validate_option_quote(low)
        _validate_option_quote(high)
        if low.underlying != high.underlying or low.expiry != high.expiry:
            raise ValueError("box legs must share underlying and expiry")
        if low.instrument_class != high.instrument_class:
            raise ValueError("box legs must share instrument class")
        if not low.strike < high.strike:
            raise ValueError("low strike must be below high strike")
        if low.timestamp_ns != high.timestamp_ns:
            raise ValueError("box legs must share timestamp")
        if low.lot_size != high.lot_size:
            raise ValueError("box legs must share lot size")
        if fees_per_unit < 0:
            raise ValueError("fees_per_unit must be non-negative")
        if liquidity is not None and (not liquidity.accepts_option(low) or not liquidity.accepts_option(high)):
            return None
        width = high.strike - low.strike
        if direction == "LONG":
            debit = low.call_ask + low.put_ask - high.call_bid - high.put_bid
            edge = width - debit - fees_per_unit
        else:
            credit = low.call_bid + low.put_bid - high.call_ask - high.put_ask
            edge = credit - width - fees_per_unit
        if edge <= 0:
            return None
        pnl = edge * low.lot_size
        return ArbitrageOpportunity("BOX", direction, low.timestamp_ns, low.underlying,
                                    low.expiry, low.strike, high.strike, edge, pnl, pnl, width)


class SyntheticCashCarryBacktester:
    """Compare executable futures with option-implied synthetic forwards."""

    @staticmethod
    def evaluate(option: OptionQuote, future: FutureQuote, *, rate: float = 0.0,
                 time_to_expiry_years: float, fees_per_unit: float = 0.0,
                 direction: Literal["LONG", "SHORT"] = "LONG",
                 liquidity: LiquidityPolicy | None = None) -> ArbitrageOpportunity | None:
        _validate_direction(direction)
        _validate_option_quote(option)
        if not isfinite(float(future.bid)) or not isfinite(float(future.ask)):
            raise ValueError("future prices must be finite")
        if future.bid < 0 or future.ask < future.bid:
            raise ValueError("future ask price must be at least bid price and bids must be non-negative")
        if future.lot_size <= 0:
            raise ValueError("future lot_size must be positive")
        if future.volume < 0 or future.oi < 0:
            raise ValueError("future volume and oi must be non-negative")
        if option.underlying != future.underlying or option.expiry != future.expiry:
            raise ValueError("synthetic legs must share underlying and expiry")
        if option.instrument_class != future.instrument_class:
            raise ValueError("synthetic legs must share instrument class")
        if option.timestamp_ns != future.timestamp_ns:
            raise ValueError("synthetic legs must share timestamp")
        if option.lot_size != future.lot_size:
            raise ValueError("synthetic legs must share lot size")
        if not isfinite(float(time_to_expiry_years)) or time_to_expiry_years < 0:
            raise ValueError("time_to_expiry_years must be finite and non-negative")
        if not isfinite(float(rate)):
            raise ValueError("rate must be finite")
        if fees_per_unit < 0 or not isfinite(float(fees_per_unit)):
            raise ValueError("fees_per_unit must be finite and non-negative")
        if liquidity is not None and (not liquidity.accepts_future(future) or not liquidity.accepts_option(option)):
            return None

        carry = exp(rate * time_to_expiry_years)
        synthetic_buy = option.strike + (option.call_ask - option.put_bid) * carry
        synthetic_sell = option.strike + (option.call_bid - option.put_ask) * carry
        if direction == "LONG":
            edge = future.bid - synthetic_buy - fees_per_unit
        else:
            edge = synthetic_sell - future.ask - fees_per_unit
        if edge <= 0:
            return None
        pnl = edge * future.lot_size
        return ArbitrageOpportunity("SYNTHETIC_CASH_CARRY", direction, future.timestamp_ns,
                                    future.underlying, future.expiry, None, None, edge, pnl, pnl,
                                    future.bid if direction == "LONG" else future.ask)


__all__ = ["ArbitrageOpportunity", "BoxSpreadBacktester", "FutureQuote", "LiquidityPolicy",
           "OptionQuote", "SyntheticCashCarryBacktester"]
