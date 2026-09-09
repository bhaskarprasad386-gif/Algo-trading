"""Executable multi-leg arbitrage primitives for liquid F&O backtests."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
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

    @staticmethod
    def _accepts_quote(*, volume: int, oi: int, bid: float, ask: float,
                       min_volume: int, min_oi: int, max_spread_pct: float) -> bool:
        if volume < min_volume or oi < min_oi:
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


class BoxSpreadBacktester:
    """Evaluate executable long/reverse boxes at one timestamp."""

    @staticmethod
    def evaluate(low: OptionQuote, high: OptionQuote, *,
                 direction: Literal["LONG", "SHORT"] = "LONG",
                 fees_per_unit: float = 0.0) -> ArbitrageOpportunity | None:
        if low.underlying != high.underlying or low.expiry != high.expiry:
            raise ValueError("box legs must share underlying and expiry")
        if not low.strike < high.strike:
            raise ValueError("low strike must be below high strike")
        if low.timestamp_ns != high.timestamp_ns:
            raise ValueError("box legs must share timestamp")
        if low.lot_size != high.lot_size:
            raise ValueError("box legs must share lot size")
        if fees_per_unit < 0:
            raise ValueError("fees_per_unit must be non-negative")
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
        if option.underlying != future.underlying or option.expiry != future.expiry:
            raise ValueError("synthetic legs must share underlying and expiry")
        if option.timestamp_ns != future.timestamp_ns:
            raise ValueError("synthetic legs must share timestamp")
        if option.lot_size != future.lot_size:
            raise ValueError("synthetic legs must share lot size")
        if time_to_expiry_years < 0:
            raise ValueError("time_to_expiry_years must be non-negative")
        if fees_per_unit < 0:
            raise ValueError("fees_per_unit must be non-negative")
        if liquidity is not None and not liquidity.accepts_future(future):
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
