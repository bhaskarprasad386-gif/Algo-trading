"""Historical adapters for the four executable arbitrage strategy families.

Adapters consume real normalized quote payloads and plug directly into
HistoricalArbitrageRunner. They never synthesize an exit: a position closes
only when a later event contains an executable reverse transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .arbitrage_backtester import BoxSpreadBacktester, FutureQuote, OptionQuote, SyntheticCashCarryBacktester
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


class BoxSpreadStrategyAdapter:
    """Long/reverse box lifecycle using executable bid/ask quotes."""

    def __init__(self, *, direction: str = "LONG", fees_per_unit: float = 0.0) -> None:
        self.direction = direction
        self.fees_per_unit = fees_per_unit

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        low = _option(_required(event, "low")); high = _option(_required(event, "high"))
        opportunity = BoxSpreadBacktester.evaluate(low, high, direction=self.direction, fees_per_unit=self.fees_per_unit)
        if opportunity is None:
            return ()
        trade_id = str(event.get("trade_id", f"BOX:{low.underlying}:{low.expiry}:{low.timestamp_ns}:{low.strike}:{high.strike}"))
        return (OpenPosition(trade_id, low.timestamp_ns, f"{low.underlying}:BOX", self.direction, low.lot_size,
                             opportunity.executable_edge, contract=f"BOX:{low.strike}:{high.strike}",
                             expiry=str(low.expiry), strike=low.strike, leg="BOX",
                             data_resolution=str(event.get("data_resolution", "")),
                             metadata={"strategy": "BOX", "high_strike": high.strike}),)

    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        low = _option(_required(event, "low")); high = _option(_required(event, "high"))
        reverse = "SHORT" if self.direction == "LONG" else "LONG"
        opportunity = BoxSpreadBacktester.evaluate(low, high, direction=reverse, fees_per_unit=self.fees_per_unit)
        if opportunity is None:
            return None
        gross = position.entry_price + opportunity.executable_edge
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge, gross,
                             fees=self.fees_per_unit * position.quantity,
                             metadata={"strategy": "BOX", "close_direction": reverse})


class SyntheticCashCarryStrategyAdapter:
    """Synthetic-vs-future lifecycle; reverse side is required to close."""

    def __init__(self, *, direction: str = "LONG", rate: float = 0.0,
                 time_to_expiry_years: float = 0.0, fees_per_unit: float = 0.0) -> None:
        self.direction = direction; self.rate = rate
        self.time_to_expiry_years = time_to_expiry_years; self.fees_per_unit = fees_per_unit

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        option = _option(_required(event, "option")); future = _future(_required(event, "future"))
        opportunity = SyntheticCashCarryBacktester.evaluate(option, future, rate=self.rate,
            time_to_expiry_years=self.time_to_expiry_years, fees_per_unit=self.fees_per_unit, direction=self.direction)
        if opportunity is None: return ()
        trade_id = str(event.get("trade_id", f"SYN:{future.underlying}:{future.expiry}:{future.timestamp_ns}:{option.strike}"))
        return (OpenPosition(trade_id, future.timestamp_ns, f"{future.underlying}:SYNTHETIC", self.direction,
            future.lot_size, opportunity.executable_edge, contract=f"SYNTHETIC:{option.strike}",
            expiry=str(future.expiry), strike=option.strike, leg="SYNTHETIC_CASH_CARRY",
            data_resolution=str(event.get("data_resolution", "")), metadata={"strategy": "SYNTHETIC_CASH_CARRY"}),)

    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        option = _option(_required(event, "option")); future = _future(_required(event, "future"))
        reverse = "SHORT" if self.direction == "LONG" else "LONG"
        opportunity = SyntheticCashCarryBacktester.evaluate(option, future, rate=self.rate,
            time_to_expiry_years=self.time_to_expiry_years, fees_per_unit=self.fees_per_unit, direction=reverse)
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge,
            position.entry_price + opportunity.executable_edge,
            fees=self.fees_per_unit * position.quantity,
            metadata={"strategy": "SYNTHETIC_CASH_CARRY", "close_direction": reverse})


class CashFutureStrategyAdapter:
    """Cash-and-carry lifecycle using real spot/future bid/ask quotes."""

    def __init__(self, *, direction: str = "LONG_CASH_SHORT_FUTURE", fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG_CASH_SHORT_FUTURE", "SHORT_CASH_LONG_FUTURE"}:
            raise ValueError("invalid cash-future direction")
        self.direction = direction; self.fees_per_unit = fees_per_unit

    @staticmethod
    def _edge(q: CashFutureQuote, direction: str) -> float:
        if q.carry_factor <= 0: raise ValueError("carry_factor must be positive")
        if direction == "LONG_CASH_SHORT_FUTURE":
            return q.future_bid - q.spot_ask * q.carry_factor
        return q.spot_bid * q.carry_factor - q.future_ask

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        q = _cash_future(_required(event, "cash_future")); edge = self._edge(q, self.direction) - self.fees_per_unit
        if edge <= 0: return ()
        trade_id = str(event.get("trade_id", f"CF:{q.underlying}:{q.expiry}:{q.timestamp_ns}"))
        return (OpenPosition(trade_id, q.timestamp_ns, f"{q.underlying}:CASH_FUTURE", self.direction,
            q.lot_size, edge, contract=f"CASH-FUTURE:{q.expiry}", expiry=str(q.expiry), leg="CASH_FUTURE",
            data_resolution=str(event.get("data_resolution", "")), metadata={"strategy": "CASH_CARRY"}),)

    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        q = _cash_future(_required(event, "cash_future"))
        reverse = "SHORT_CASH_LONG_FUTURE" if self.direction == "LONG_CASH_SHORT_FUTURE" else "LONG_CASH_SHORT_FUTURE"
        edge = self._edge(q, reverse)
        if edge <= 0: return None
        return ExitExecution(q.timestamp_ns, edge, position.entry_price + edge,
            fees=self.fees_per_unit * position.quantity,
            metadata={"strategy": "CASH_CARRY", "close_direction": reverse})


class CalendarSpreadStrategyAdapter:
    """Near/far expiry lifecycle; both expiries remain explicit."""

    def __init__(self, *, direction: str = "LONG_NEAR_SHORT_FAR", fees_per_unit: float = 0.0) -> None:
        self.direction = direction; self.fees_per_unit = fees_per_unit

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        near = _calendar(_required(event, "near")); far = _calendar(_required(event, "far"))
        opportunity = CalendarSpreadBacktester.evaluate(near, far, direction=self.direction, fees_per_unit=self.fees_per_unit)
        if opportunity is None: return ()
        trade_id = str(event.get("trade_id", f"CAL:{near.underlying}:{near.expiry}:{far.expiry}:{near.timestamp_ns}"))
        return (OpenPosition(trade_id, near.timestamp_ns, f"{near.underlying}:CALENDAR", self.direction, near.lot_size,
            opportunity.executable_edge, contract=f"CALENDAR:{near.expiry}:{far.expiry}", expiry=str(near.expiry),
            strike=near.strike, leg="CALENDAR", data_resolution=str(event.get("data_resolution", "")),
            metadata={"strategy": "CALENDAR", "near_expiry": near.expiry, "far_expiry": far.expiry,
                      "option_type": near.option_type}),)

    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        near = _calendar(_required(event, "near")); far = _calendar(_required(event, "far"))
        reverse = "SHORT_NEAR_LONG_FAR" if self.direction == "LONG_NEAR_SHORT_FAR" else "LONG_NEAR_SHORT_FAR"
        opportunity = CalendarSpreadBacktester.evaluate(near, far, direction=reverse, fees_per_unit=self.fees_per_unit)
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge,
            position.entry_price + opportunity.executable_edge,
            fees=self.fees_per_unit * position.quantity,
            metadata={"strategy": "CALENDAR", "close_direction": reverse,
                      "near_expiry": near.expiry, "far_expiry": far.expiry})


__all__ = ["BoxSpreadStrategyAdapter", "CalendarSpreadStrategyAdapter", "CashFutureQuote",
           "CashFutureStrategyAdapter", "SyntheticCashCarryStrategyAdapter"]
