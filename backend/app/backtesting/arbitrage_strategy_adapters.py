"""Historical execution adapter for the retained Box Spread strategy."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .arbitrage_backtester import BoxSpreadBacktester, OptionQuote
from .historical_arbitrage_runner import ExitExecution, OpenPosition


def _option(value: Mapping[str, Any]) -> OptionQuote:
    return OptionQuote(**value)


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
        self.direction = direction
        self.fees_per_unit = fees_per_unit

    def entry(self, event: Mapping[str, Any]) -> Iterable[OpenPosition]:
        low = _option(_required(event, "low"))
        high = _option(_required(event, "high"))
        opportunity = BoxSpreadBacktester.evaluate(
            low, high, direction=self.direction, fees_per_unit=0.0
        )
        if opportunity is None:
            return ()
        trade_id = str(
            event.get(
                "trade_id",
                f"BOX:{low.underlying}:{low.expiry}:{low.timestamp_ns}:{low.strike}:{high.strike}",
            )
        )
        return (
            OpenPosition(
                trade_id,
                low.timestamp_ns,
                f"{low.underlying}:BOX",
                self.direction,
                low.lot_size,
                opportunity.executable_edge,
                contract=f"BOX:{low.strike}:{high.strike}",
                expiry=str(low.expiry),
                strike=low.strike,
                leg="BOX",
                data_resolution=str(event.get("data_resolution", "")),
                metadata={"strategy": "BOX", "high_strike": high.strike},
            ),
        )

    def exit(self, position: OpenPosition, event: Mapping[str, Any]) -> ExitExecution | None:
        low = _option(_required(event, "low"))
        high = _option(_required(event, "high"))
        reverse = "SHORT" if self.direction == "LONG" else "LONG"
        opportunity = BoxSpreadBacktester.evaluate(
            low, high, direction=reverse, fees_per_unit=0.0
        )
        if opportunity is None:
            return None
        return ExitExecution(
            opportunity.timestamp_ns,
            opportunity.executable_edge,
            position.entry_price + opportunity.executable_edge,
            fees=2.0 * self.fees_per_unit * position.quantity,
            metadata={"strategy": "BOX", "close_direction": reverse},
        )


__all__ = ["BoxSpreadStrategyAdapter"]
