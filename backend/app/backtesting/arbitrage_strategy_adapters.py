"""Historical adapters for the four executable arbitrage strategy families."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

from .arbitrage_backtester import (
    BoxSpreadBacktester, FutureQuote, LiquidityPolicy, OptionQuote,
    SyntheticCashCarryBacktester,
)
from .calendar_spread import CalendarQuote, CalendarSpreadBacktester
from .historical_arbitrage_runner import ExitExecution, OpenPosition


@dataclass(frozen=True)
class CashFutureQuote:
    timestamp_ns: int
    underlying: str
    spot_bid: float
    spot_ask: float
    future_bid: float
    future_ask: float
    expiry: int
    lot_size: int = 1
    carry_factor: float = 1.0
    instrument_class: str = "STOCK"

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("cash-future timestamp_ns must be a non-negative integer")
        if not self.underlying.strip():
            raise ValueError("cash-future underlying is required")
        if isinstance(self.expiry, bool) or not isinstance(self.expiry, int) or self.expiry <= 0:
            raise ValueError("cash-future expiry must be a positive integer")
        if isinstance(self.lot_size, bool) or not isinstance(self.lot_size, int) or self.lot_size <= 0:
            raise ValueError("cash-future lot_size must be a positive integer")


def _option(value: Mapping[str, Any]) -> OptionQuote:
    return OptionQuote(**value)

def _future(value: Mapping[str, Any]) -> FutureQuote:
    return FutureQuote(**value)

def _calendar(value: Mapping[str, Any]) -> CalendarQuote:
    return CalendarQuote(**value)

def _cash_future(value: Mapping[str, Any]) -> CashFutureQuote:
    return CashFutureQuote(**value)

def _required(event: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = event.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} quote payload is required")
    return value

def _liquidity(event: Mapping[str, Any]) -> LiquidityPolicy | None:
    value = event.get("liquidity")
    if value is None:
        return None
    if not isinstance(value, LiquidityPolicy):
        raise ValueError("liquidity must be a LiquidityPolicy")
    return value

def _fee(value: float) -> float:
    if not isfinite(float(value)) or value < 0:
        raise ValueError("fees_per_unit must be finite and non-negative")
    return float(value)


class BoxSpreadStrategyAdapter:
    def __init__(self, *, direction: str = "LONG", fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG", "SHORT"}: raise ValueError("invalid box adapter configuration")
        self.direction = direction; self.fees_per_unit = _fee(fees_per_unit)
    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        low = _option(_required(event, "low")); high = _option(_required(event, "high"))
        opportunity = BoxSpreadBacktester.evaluate(low, high, direction=self.direction, fees_per_unit=0.0, liquidity=_liquidity(event))
        if opportunity is None: return ()
        trade_id = str(event.get("trade_id", f"BOX:{low.underlying}:{low.expiry}:{low.timestamp_ns}:{low.strike}:{high.strike}"))
        entry_price = low.call_ask + low.put_ask - high.call_bid - high.put_bid if self.direction == "LONG" else high.call_ask + high.put_ask - low.call_bid - low.put_bid
        return (OpenPosition(trade_id, low.timestamp_ns, f"{low.underlying}:BOX", self.direction, low.lot_size, entry_price, contract=f"BOX:{low.strike}:{high.strike}", expiry=str(low.expiry), strike=low.strike, leg="BOX", data_resolution=str(event.get("data_resolution", "")), metadata={"strategy": "BOX", "high_strike": high.strike}, entry_fees=self.fees_per_unit * low.lot_size),)
    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        low = _option(_required(event, "low")); high = _option(_required(event, "high"))
        reverse = "SHORT" if self.direction == "LONG" else "LONG"
        opportunity = BoxSpreadBacktester.evaluate(low, high, direction=reverse, fees_per_unit=0.0, liquidity=_liquidity(event))
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge, position.entry_price + opportunity.executable_edge, fees=self.fees_per_unit * position.quantity, metadata={"strategy": "BOX", "close_direction": reverse})


class SyntheticCashCarryStrategyAdapter:
    def __init__(self, *, direction: str = "LONG", rate: float = 0.0, time_to_expiry_years: float = 0.0, fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG", "SHORT"} or not isfinite(float(rate)): raise ValueError("invalid synthetic adapter configuration")
        if not isfinite(float(time_to_expiry_years)) or time_to_expiry_years < 0: raise ValueError("time_to_expiry_years must be finite and non-negative")
        self.direction = direction; self.rate = float(rate); self.time_to_expiry_years = float(time_to_expiry_years); self.fees_per_unit = _fee(fees_per_unit)
    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        option = _option(_required(event, "option")); future = _future(_required(event, "future"))
        opportunity = SyntheticCashCarryBacktester.evaluate(option, future, rate=self.rate, time_to_expiry_years=self.time_to_expiry_years, fees_per_unit=0.0, direction=self.direction, liquidity=_liquidity(event))
        if opportunity is None: return ()
        trade_id = str(event.get("trade_id", f"SYN:{future.underlying}:{future.expiry}:{future.timestamp_ns}:{option.strike}"))
        return (OpenPosition(trade_id, future.timestamp_ns, f"{future.underlying}:SYNTHETIC", self.direction, future.lot_size, opportunity.executable_edge, contract=f"SYNTHETIC:{option.strike}", expiry=str(future.expiry), strike=option.strike, leg="SYNTHETIC_CASH_CARRY", data_resolution=str(event.get("data_resolution", "")), metadata={"strategy": "SYNTHETIC_CASH_CARRY"}, entry_fees=self.fees_per_unit * future.lot_size),)
    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        option = _option(_required(event, "option")); future = _future(_required(event, "future")); reverse = "SHORT" if self.direction == "LONG" else "LONG"
        opportunity = SyntheticCashCarryBacktester.evaluate(option, future, rate=self.rate, time_to_expiry_years=self.time_to_expiry_years, fees_per_unit=0.0, direction=reverse, liquidity=_liquidity(event))
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge, position.entry_price + opportunity.executable_edge, fees=self.fees_per_unit * position.quantity, metadata={"strategy": "SYNTHETIC_CASH_CARRY", "close_direction": reverse})


class CashFutureStrategyAdapter:
    def __init__(self, *, direction: str = "LONG_CASH_SHORT_FUTURE", fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG_CASH_SHORT_FUTURE", "SHORT_CASH_LONG_FUTURE"}: raise ValueError("invalid cash-future adapter configuration")
        self.direction = direction; self.fees_per_unit = _fee(fees_per_unit)
    @staticmethod
    def _edge(q: CashFutureQuote, direction: str) -> float:
        values = (q.spot_bid, q.spot_ask, q.future_bid, q.future_ask, q.carry_factor)
        if not all(isfinite(float(value)) for value in values) or q.carry_factor <= 0: raise ValueError("cash/future quote values must be finite and valid")
        if q.spot_bid < 0 or q.spot_ask < q.spot_bid or q.future_bid < 0 or q.future_ask < q.future_bid: raise ValueError("invalid cash/future quote")
        if direction == "LONG_CASH_SHORT_FUTURE": return q.future_bid - q.spot_ask * q.carry_factor
        if direction == "SHORT_CASH_LONG_FUTURE": return q.spot_bid * q.carry_factor - q.future_ask
        raise ValueError("invalid cash-future direction")
    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        q = _cash_future(_required(event, "cash_future")); edge = self._edge(q, self.direction)
        if edge <= self.fees_per_unit: return ()
        trade_id = str(event.get("trade_id", f"CF:{q.underlying}:{q.expiry}:{q.timestamp_ns}"))
        return (OpenPosition(trade_id, q.timestamp_ns, f"{q.underlying}:CASH_FUTURE", self.direction, q.lot_size, edge, contract=f"CASH-FUTURE:{q.expiry}", expiry=str(q.expiry), leg="CASH_FUTURE", data_resolution=str(event.get("data_resolution", "")), metadata={"strategy": "CASH_CARRY"}, entry_fees=self.fees_per_unit * q.lot_size),)
    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        q = _cash_future(_required(event, "cash_future")); reverse = "SHORT_CASH_LONG_FUTURE" if self.direction == "LONG_CASH_SHORT_FUTURE" else "LONG_CASH_SHORT_FUTURE"; edge = self._edge(q, reverse)
        if edge <= 0: return None
        return ExitExecution(q.timestamp_ns, edge, position.entry_price + edge, fees=self.fees_per_unit * position.quantity, metadata={"strategy": "CASH_CARRY", "close_direction": reverse})


class CalendarSpreadStrategyAdapter:
    def __init__(self, *, direction: str = "LONG_NEAR_SHORT_FAR", fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG_NEAR_SHORT_FAR", "SHORT_NEAR_LONG_FAR"}: raise ValueError("invalid calendar adapter configuration")
        self.direction = direction; self.fees_per_unit = _fee(fees_per_unit)
    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        near = _calendar(_required(event, "near")); far = _calendar(_required(event, "far"))
        opportunity = CalendarSpreadBacktester.evaluate(near, far, direction=self.direction, fees_per_unit=0.0, liquidity=_liquidity(event))
        if opportunity is None: return ()
        trade_id = str(event.get("trade_id", f"CAL:{near.underlying}:{near.expiry}:{far.expiry}:{near.timestamp_ns}"))
        return (OpenPosition(trade_id, near.timestamp_ns, f"{near.underlying}:CALENDAR", self.direction, near.lot_size, opportunity.executable_edge, contract=f"CALENDAR:{near.expiry}:{far.expiry}", expiry=str(near.expiry), strike=near.strike, leg="CALENDAR", data_resolution=str(event.get("data_resolution", "")), metadata={"strategy": "CALENDAR", "near_expiry": near.expiry, "far_expiry": far.expiry, "option_type": near.option_type}, entry_fees=self.fees_per_unit * near.lot_size),)
    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        near = _calendar(_required(event, "near")); far = _calendar(_required(event, "far")); reverse = "SHORT_NEAR_LONG_FAR" if self.direction == "LONG_NEAR_SHORT_FAR" else "LONG_NEAR_SHORT_FAR"
        opportunity = CalendarSpreadBacktester.evaluate(near, far, direction=reverse, fees_per_unit=0.0, liquidity=_liquidity(event))
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge, position.entry_price + opportunity.executable_edge, fees=self.fees_per_unit * position.quantity, metadata={"strategy": "CALENDAR", "close_direction": reverse, "near_expiry": near.expiry, "far_expiry": far.expiry})
