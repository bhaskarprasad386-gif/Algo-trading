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
from .contracts import MultiLegStrategyProtocol
from .engine import EventContext
from .execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder


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
        if direction not in {"LONG", "SHORT"} or fees_per_unit < 0:
            raise ValueError("invalid box adapter configuration")
        self.direction = direction; self.fees_per_unit = fees_per_unit

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        low = _option(_required(event, "low")); high = _option(_required(event, "high"))
        opportunity = BoxSpreadBacktester.evaluate(low, high, direction=self.direction, fees_per_unit=0.0)
        if opportunity is None: return ()
        trade_id = str(event.get("trade_id", f"BOX:{low.underlying}:{low.expiry}:{low.timestamp_ns}:{low.strike}:{high.strike}"))
        if self.direction == "LONG":
            entry_price = low.call_ask + low.put_ask - high.call_bid - high.put_bid
        else:
            entry_price = high.call_ask + high.put_ask - low.call_bid - low.put_bid
        return (OpenPosition(trade_id, low.timestamp_ns, f"{low.underlying}:BOX", self.direction, low.lot_size,
            entry_price, contract=f"BOX:{low.strike}:{high.strike}", expiry=str(low.expiry),
            strike=low.strike, leg="BOX", data_resolution=str(event.get("data_resolution", "")),
            metadata={"strategy": "BOX", "high_strike": high.strike}),)

    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        low = _option(_required(event, "low")); high = _option(_required(event, "high"))
        reverse = "SHORT" if self.direction == "LONG" else "LONG"
        opportunity = BoxSpreadBacktester.evaluate(low, high, direction=reverse, fees_per_unit=0.0)
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge,
            position.entry_price + opportunity.executable_edge,
            fees=2.0 * self.fees_per_unit * position.quantity,
            metadata={"strategy": "BOX", "close_direction": reverse})


class SyntheticCashCarryStrategyAdapter:
    """Synthetic-vs-future lifecycle; reverse side is required to close."""

    def __init__(self, *, direction: str = "LONG", rate: float = 0.0,
                 time_to_expiry_years: float = 0.0, fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG", "SHORT"} or time_to_expiry_years < 0 or fees_per_unit < 0:
            raise ValueError("invalid synthetic adapter configuration")
        self.direction = direction; self.rate = rate
        self.time_to_expiry_years = time_to_expiry_years; self.fees_per_unit = fees_per_unit

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        option = _option(_required(event, "option")); future = _future(_required(event, "future"))
        opportunity = SyntheticCashCarryBacktester.evaluate(option, future, rate=self.rate,
            time_to_expiry_years=self.time_to_expiry_years, fees_per_unit=0.0, direction=self.direction)
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
            time_to_expiry_years=self.time_to_expiry_years, fees_per_unit=0.0, direction=reverse)
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge,
            position.entry_price + opportunity.executable_edge,
            fees=2.0 * self.fees_per_unit * position.quantity,
            metadata={"strategy": "SYNTHETIC_CASH_CARRY", "close_direction": reverse})


class CashFutureStrategyAdapter:
    """Cash-and-carry lifecycle using real spot/future bid/ask quotes."""

    def __init__(self, *, direction: str = "LONG_CASH_SHORT_FUTURE", fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG_CASH_SHORT_FUTURE", "SHORT_CASH_LONG_FUTURE"} or fees_per_unit < 0:
            raise ValueError("invalid cash-future adapter configuration")
        self.direction = direction; self.fees_per_unit = fees_per_unit

    @staticmethod
    def _edge(q: CashFutureQuote, direction: str) -> float:
        if q.carry_factor <= 0: raise ValueError("carry_factor must be positive")
        if q.spot_bid < 0 or q.spot_ask < q.spot_bid or q.future_bid < 0 or q.future_ask < q.future_bid:
            raise ValueError("invalid cash/future quote")
        if direction == "LONG_CASH_SHORT_FUTURE": return q.future_bid - q.spot_ask * q.carry_factor
        return q.spot_bid * q.carry_factor - q.future_ask

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        q = _cash_future(_required(event, "cash_future")); edge = self._edge(q, self.direction)
        if edge <= 0: return ()
        trade_id = str(event.get("trade_id", f"CF:{q.underlying}:{q.expiry}:{q.timestamp_ns}"))
        return (OpenPosition(trade_id, q.timestamp_ns, f"{q.underlying}:CASH_FUTURE", self.direction, q.lot_size,
            edge, contract=f"CASH-FUTURE:{q.expiry}", expiry=str(q.expiry), leg="CASH_FUTURE",
            data_resolution=str(event.get("data_resolution", "")), metadata={"strategy": "CASH_CARRY"}),)

    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        q = _cash_future(_required(event, "cash_future"))
        reverse = "SHORT_CASH_LONG_FUTURE" if self.direction == "LONG_CASH_SHORT_FUTURE" else "LONG_CASH_SHORT_FUTURE"
        edge = self._edge(q, reverse)
        if edge <= 0: return None
        return ExitExecution(q.timestamp_ns, edge, position.entry_price + edge,
            fees=2.0 * self.fees_per_unit * position.quantity,
            metadata={"strategy": "CASH_CARRY", "close_direction": reverse})


class CashFutureUniversalMultiLegAdapter:
    """Atomic cash/future strategy for the Universal multi-leg engine."""

    def __init__(self, *, direction: str = "LONG_CASH_SHORT_FUTURE", quantity: int = 1) -> None:
        if direction not in {"LONG_CASH_SHORT_FUTURE", "SHORT_CASH_LONG_FUTURE"}:
            raise ValueError("invalid cash-future adapter direction")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        self.direction = direction
        self.quantity = quantity
        self._open = False

    @staticmethod
    def _quote(payload: object, name: str) -> tuple[float, float, int, int]:
        if not isinstance(payload, Mapping):
            raise ValueError(f"{name} quote payload is required")
        try:
            bid = float(payload["bid"])
            ask = float(payload["ask"])
            bid_quantity = int(payload["bid_quantity"])
            ask_quantity = int(payload["ask_quantity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{name} quote must contain bid/ask and bid_quantity/ask_quantity") from exc
        if bid <= 0 or ask <= 0 or ask < bid or bid_quantity <= 0 or ask_quantity <= 0:
            raise ValueError(f"invalid {name} executable quote")
        return bid, ask, bid_quantity, ask_quantity

    @staticmethod
    def _book(bid: float, ask: float, bid_quantity: int, ask_quantity: int) -> OrderBook:
        return OrderBook(
            bids=(DepthLevel(bid, bid_quantity),),
            asks=(DepthLevel(ask, ask_quantity),),
        )

    @staticmethod
    def _leg_instrument(context: EventContext, event_key: str) -> str:
        metadata = context.payload.get("__replay_legs__")
        if not isinstance(metadata, Mapping):
            raise ValueError("Universal cash-future replay identity metadata is required")
        leg = metadata.get(event_key)
        if not isinstance(leg, Mapping) or not isinstance(leg.get("instrument"), str) or not leg["instrument"].strip():
            raise ValueError(f"exact {event_key} instrument identity is required")
        return leg["instrument"]

    def __call__(self, context: EventContext) -> Iterable[tuple[SimOrder, OrderBook, int]]:
        cash = context.payload.get("cash")
        future = context.payload.get("future")
        cash_bid, cash_ask, cash_bid_qty, cash_ask_qty = self._quote(cash, "cash")
        future_bid, future_ask, future_bid_qty, future_ask_qty = self._quote(future, "future")

        if not self._open:
            if self.direction == "LONG_CASH_SHORT_FUTURE":
                edge = future_bid - cash_ask
                if edge <= 0:
                    return ()
                cash_side, future_side = ExecutionSide.BUY, ExecutionSide.SELL
            else:
                edge = cash_bid - future_ask
                if edge <= 0:
                    return ()
                cash_side, future_side = ExecutionSide.SELL, ExecutionSide.BUY
            phase = "OPEN"
            self._open = True
        else:
            if self.direction == "LONG_CASH_SHORT_FUTURE":
                edge = cash_bid - future_ask
                if edge <= 0:
                    return ()
                cash_side, future_side = ExecutionSide.SELL, ExecutionSide.BUY
            else:
                edge = future_bid - cash_ask
                if edge <= 0:
                    return ()
                cash_side, future_side = ExecutionSide.BUY, ExecutionSide.SELL
            phase = "CLOSE"
            self._open = False

        cash_instrument = self._leg_instrument(context, "cash")
        future_instrument = self._leg_instrument(context, "future")
        cash_book = self._book(cash_bid, cash_ask, cash_bid_qty, cash_ask_qty)
        future_book = self._book(future_bid, future_ask, future_bid_qty, future_ask_qty)
        prefix = f"CF:{context.timestamp_ns}:{phase}"
        return (
            (
                SimOrder(f"{prefix}:CASH", cash_instrument, cash_side, self.quantity,
                         OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                cash_book,
                context.timestamp_ns,
            ),
            (
                SimOrder(f"{prefix}:FUTURE", future_instrument, future_side, self.quantity,
                         OrderType.MARKET, submitted_at_ns=context.timestamp_ns),
                future_book,
                context.timestamp_ns,
            ),
        )


class CalendarSpreadStrategyAdapter:
    """Near/far expiry lifecycle; both expiries remain explicit."""

    def __init__(self, *, direction: str = "LONG_NEAR_SHORT_FAR", fees_per_unit: float = 0.0) -> None:
        if direction not in {"LONG_NEAR_SHORT_FAR", "SHORT_NEAR_LONG_FAR"} or fees_per_unit < 0:
            raise ValueError("invalid calendar adapter configuration")
        self.direction = direction; self.fees_per_unit = fees_per_unit

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        near = _calendar(_required(event, "near")); far = _calendar(_required(event, "far"))
        opportunity = CalendarSpreadBacktester.evaluate(near, far, direction=self.direction, fees_per_unit=0.0)
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
        opportunity = CalendarSpreadBacktester.evaluate(near, far, direction=reverse, fees_per_unit=0.0)
        if opportunity is None: return None
        return ExitExecution(opportunity.timestamp_ns, opportunity.executable_edge,
            position.entry_price + opportunity.executable_edge,
            fees=2.0 * self.fees_per_unit * position.quantity,
            metadata={"strategy": "CALENDAR", "close_direction": reverse,
                      "near_expiry": near.expiry, "far_expiry": far.expiry})
