"""Historical adapters for the four executable arbitrage strategy families.

Adapters consume real normalized quote payloads and plug directly into
HistoricalArbitrageRunner. They never synthesize an exit: a position closes
only when a later event contains an executable reverse transaction.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .arbitrage_backtester import BoxSpreadBacktester, FutureQuote, OptionQuote, SyntheticCashCarryBacktester
from .calendar_spread import CalendarQuote, CalendarSpreadBacktester
from .historical_arbitrage_runner import ExitExecution, OpenPosition
from .contracts import AtomicTradeReportInput, MultiLegStrategyProtocol
from .engine import EventContext
from .execution import DepthLevel, ExecutionSide, OrderBook, OrderType, SimOrder
from .result_ledger import BacktestTrade


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
        # Persist the same executable entry-edge semantics used by the
        # arbitrage primitive; do not reconstruct a different price formula here.
        entry_price = opportunity.executable_edge
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
        self._pending_open: bool | None = None
        self._open_cash_instrument: str | None = None
        self._open_future_instrument: str | None = None
        self._pending_cash_instrument: str | None = None
        self._pending_future_instrument: str | None = None

    def get_state(self) -> dict[str, Any]:
        return {
            "open": self._open,
            "pending_open": self._pending_open,
            "open_cash_instrument": self._open_cash_instrument,
            "open_future_instrument": self._open_future_instrument,
            "pending_cash_instrument": self._pending_cash_instrument,
            "pending_future_instrument": self._pending_future_instrument,
        }

    def set_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise ValueError("cash-future strategy state must be a mapping")
        open_value = state.get("open", False)
        pending_open = state.get("pending_open")
        if not isinstance(open_value, bool):
            raise ValueError("cash-future strategy state open must be boolean")
        if pending_open is not None and not isinstance(pending_open, bool):
            raise ValueError("cash-future strategy state pending_open must be boolean or None")
        values = {
            "open_cash_instrument": state.get("open_cash_instrument"),
            "open_future_instrument": state.get("open_future_instrument"),
            "pending_cash_instrument": state.get("pending_cash_instrument"),
            "pending_future_instrument": state.get("pending_future_instrument"),
        }
        for name, value in values.items():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"cash-future strategy state {name} must be a non-empty string or None")
        if open_value and (values["open_cash_instrument"] is None or values["open_future_instrument"] is None):
            raise ValueError("open cash-future strategy state requires both contract identities")
        if pending_open is True and (values["pending_cash_instrument"] is None or values["pending_future_instrument"] is None):
            raise ValueError("pending open cash-future state requires both contract identities")
        self._open = open_value
        self._pending_open = pending_open
        self._open_cash_instrument = values["open_cash_instrument"]
        self._open_future_instrument = values["open_future_instrument"]
        self._pending_cash_instrument = values["pending_cash_instrument"]
        self._pending_future_instrument = values["pending_future_instrument"]

    def on_atomic_execution(self, result) -> None:
        if self._pending_open is None:
            return
        if result.rejected:
            self._pending_open = None
            self._pending_cash_instrument = None
            self._pending_future_instrument = None
            return
        self._open = self._pending_open
        self._pending_open = None
        if self._open:
            self._open_cash_instrument = self._pending_cash_instrument
            self._open_future_instrument = self._pending_future_instrument
        else:
            self._open_cash_instrument = None
            self._open_future_instrument = None
        self._pending_cash_instrument = None
        self._pending_future_instrument = None

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
            self._pending_open = True
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
            self._pending_open = False

        current_cash_instrument = self._leg_instrument(context, "cash")
        current_future_instrument = self._leg_instrument(context, "future")
        if self._open:
            cash_instrument = self._open_cash_instrument
            future_instrument = self._open_future_instrument
            if cash_instrument is None or future_instrument is None:
                raise ValueError("open cash-future contract identity is unavailable")
        else:
            cash_instrument = current_cash_instrument
            future_instrument = current_future_instrument
            self._pending_cash_instrument = cash_instrument
            self._pending_future_instrument = future_instrument
        cash_book = self._book(cash_bid, cash_ask, cash_bid_qty, cash_ask_qty)
        future_book = self._book(future_bid, future_ask, future_bid_qty, future_ask_qty)
        event_token = f"{context.timestamp_ns}:{context.source}:{context.instrument}:{context.sequence if context.sequence is not None else 'NA'}"
        prefix = f"CF:{event_token}:{phase}"
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


class CashFutureTradeReporter:
    """Converts completed Universal cash/future executions into durable trade records."""

    def __init__(self, writer: Any) -> None:
        if not hasattr(writer, "record_trades"):
            raise TypeError("writer must provide record_trades")
        self.writer = writer
        self._open: dict[str, dict[str, Any]] = {}
        self._sequence = 0

    def get_state(self) -> dict[str, Any]:
        return {
            "open": {key: dict(value) for key, value in self._open.items()},
            "sequence": self._sequence,
        }

    def set_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise TypeError("reporter state must be a mapping")
        opened = state.get("open", {})
        sequence = state.get("sequence", 0)
        if not isinstance(opened, Mapping):
            raise ValueError("reporter open state must be a mapping")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("reporter sequence must be a non-negative integer")
        self._open = {str(instrument): dict(value) for instrument, value in opened.items()}
        self._sequence = sequence

    @staticmethod
    def _leg_evidence(report: AtomicTradeReportInput, index: int) -> tuple[Any, tuple[Any, ...]]:
        result = report.execution.leg_results[index]
        fills = tuple(result.fills)
        accounting = tuple(
            trade for trade in report.accounting_trades
            if any(fill.order_id == trade.order_id and fill.instrument == trade.instrument for fill in fills)
        )
        if len(accounting) != len(fills):
            raise ValueError("accounting trade evidence must match execution fills")
        fill_quantities = sorted(
            (fill.order_id, fill.instrument, fill.quantity) for fill in fills
        )
        trade_quantities = sorted(
            (trade.order_id, trade.instrument, trade.quantity) for trade in accounting
        )
        if fill_quantities != trade_quantities:
            raise ValueError("accounting trade quantities must match execution fills")
        return result, accounting

    @staticmethod
    def _weighted_price(trades: tuple[Any, ...]) -> float:
        quantity = sum(trade.quantity for trade in trades)
        if quantity <= 0:
            raise ValueError("trade quantity must be positive")
        return sum(trade.price * trade.quantity for trade in trades) / quantity

    @staticmethod
    def _slippage(result: Any, accounting: tuple[Any, ...]) -> float:
        if len(result.reference_prices) != len(result.fills):
            raise ValueError("reference prices must align with execution fills")
        total = 0.0
        by_fill_key: dict[tuple[str, str], deque[Any]] = defaultdict(deque)
        for trade in accounting:
            by_fill_key[(trade.order_id, trade.instrument)].append(trade)
        for fill, reference in zip(result.fills, result.reference_prices):
            candidates = by_fill_key.get((fill.order_id, fill.instrument))
            if not candidates:
                raise ValueError("accounting trade evidence is missing for execution fill")
            trade = candidates.popleft()
            adverse = (trade.price - reference) if fill.side == ExecutionSide.BUY else (reference - trade.price)
            total += max(0.0, adverse) * trade.quantity
        if any(candidates for candidates in by_fill_key.values()):
            raise ValueError("accounting trade evidence contains unmatched execution trades")
        return total

    @staticmethod
    def _executable_edge(results: tuple[Any, Any]) -> float:
        cash, future = results
        if not cash.fills or not future.fills:
            raise ValueError("cash-future edge requires both leg fills")
        if len(cash.reference_prices) != len(cash.fills) or len(future.reference_prices) != len(future.fills):
            raise ValueError("reference prices must align with execution fills")
        cash_reference = sum(
            ref * fill.quantity for fill, ref in zip(cash.fills, cash.reference_prices)
        ) / sum(fill.quantity for fill in cash.fills)
        future_reference = sum(
            ref * fill.quantity for fill, ref in zip(future.fills, future.reference_prices)
        ) / sum(fill.quantity for fill in future.fills)
        if cash.fills[0].side == ExecutionSide.BUY and future.fills[0].side == ExecutionSide.SELL:
            return future_reference - cash_reference
        if cash.fills[0].side == ExecutionSide.SELL and future.fills[0].side == ExecutionSide.BUY:
            return cash_reference - future_reference
        raise ValueError("cash-future legs must have opposite executable sides")

    def record_atomic_trade(self, report: AtomicTradeReportInput) -> None:
        if report.execution.rejected or not report.accounting_trades:
            return
        if len(report.execution.leg_results) != 2:
            raise ValueError("Universal cash-future trade reporting requires exactly two legs")
        completed: list[BacktestTrade] = []
        lifecycle_trade_id: str | None = None
        leg_results = (report.execution.leg_results[0], report.execution.leg_results[1])
        atomic_edge = self._executable_edge(leg_results)
        for index, result in enumerate(report.execution.leg_results):
            result, accounting = self._leg_evidence(report, index)
            if index not in (0, 1) or not result.fills:
                raise ValueError("Universal cash-future leg must contain at least one fill")
            if len(result.reference_prices) != len(result.fills):
                raise ValueError("reference prices must align with execution fills")
            instrument = result.fills[0].instrument
            quantity = sum(trade.quantity for trade in accounting)
            actual_price = self._weighted_price(accounting)
            reference_price = sum(
                ref * fill.quantity
                for fill, ref in zip(result.fills, result.reference_prices)
            ) / sum(fill.quantity for fill in result.fills)
            if instrument not in self._open:
                self._open[instrument] = {
                    "timestamp_ns": min(trade.timestamp_ns for trade in accounting),
                    "entry_price": actual_price,
                    "entry_reference_price": reference_price,
                    "entry_side": result.fills[0].side.value,
                    "entry_quantity": quantity,
                    "entry_fees": sum(trade.fee for trade in accounting),
                    "entry_slippage": self._slippage(result, accounting),
                    "entry_edge": atomic_edge,
                    "entry_order_id": result.fills[0].order_id,
                }
                continue

            opened = self._open.pop(instrument)
            if quantity != opened["entry_quantity"]:
                raise ValueError("Universal cash-future trade must close the opened quantity atomically")
            gross_pnl = sum(trade.realized_pnl_delta for trade in accounting)
            fees = opened["entry_fees"] + sum(trade.fee for trade in accounting)
            slippage = opened["entry_slippage"] + self._slippage(result, accounting)
            exit_timestamp = max(trade.timestamp_ns for trade in accounting)
            lifecycle_trade_id = lifecycle_trade_id or f"CF:{opened['entry_order_id']}"
            trade_id = f"{lifecycle_trade_id}:{'CASH' if index == 0 else 'FUTURE'}"
            completed.append(BacktestTrade(
                trade_id=trade_id,
                sequence=self._sequence,
                timestamp_ns=exit_timestamp,
                instrument=instrument,
                side=opened["entry_side"],
                quantity=quantity,
                entry_price=opened["entry_price"],
                exit_price=actual_price,
                gross_pnl=gross_pnl,
                fees=fees,
                slippage=slippage,
                net_pnl=gross_pnl - fees,
                contract=instrument,
                leg="CASH" if index == 0 else "FUTURE",
                metadata={
                    "strategy": "CASH_CARRY_UNIVERSAL",
                    "lifecycle_trade_id": lifecycle_trade_id,
                    "entry_timestamp_ns": opened["timestamp_ns"],
                    "exit_timestamp_ns": exit_timestamp,
                    "entry_reference_price": opened["entry_reference_price"],
                    "exit_reference_price": reference_price,
                    "entry_edge": opened["entry_edge"],
                    "exit_edge": atomic_edge,
                    "pricing_model": "EXECUTABLE_EDGE",
                    "entry_order_id": opened["entry_order_id"],
                    "exit_order_id": result.fills[0].order_id,
                },
            ))
            self._sequence += 1
        if completed:
            self.writer.record_trades(tuple(completed))


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
