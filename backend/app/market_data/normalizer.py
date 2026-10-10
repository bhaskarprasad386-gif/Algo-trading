"""Normalize Angel One SmartAPI WebSocket payloads into canonical market-data records.

Batch K is the broker-to-canonical boundary. It preserves the raw provider
payload while emitting one immutable strategy-neutral MarketDataRecord.
"""
from __future__ import annotations

from math import isfinite
from time import time_ns
from typing import Any, Mapping

from .contracts import InstrumentKey, InstrumentType, MarketDataRecord, OptionType
from .registry import InstrumentDescriptor


class AngelOneTickNormalizer:
    """Convert provider payload aliases/units into the canonical contract."""

    _TYPE_MAP = {
        "equity": InstrumentType.EQUITY,
        "index": InstrumentType.INDEX,
        "future": InstrumentType.FUTURE,
        "option": InstrumentType.OPTION,
        "commodity": InstrumentType.COMMODITY,
    }

    def __init__(self, price_scale: float = 100.0) -> None:
        scale = float(price_scale)
        if not isfinite(scale) or scale <= 0:
            raise ValueError("price_scale must be finite and positive")
        self.price_scale = scale

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if isfinite(number) else None

    @staticmethod
    def _number(payload: Mapping[str, Any], *names: str) -> float | None:
        for name in names:
            value = payload.get(name)
            if value is None:
                continue
            number = AngelOneTickNormalizer._finite_float(value)
            if number is not None:
                return number
        return None

    @staticmethod
    def _integer(payload: Mapping[str, Any], *names: str) -> int | None:
        value = AngelOneTickNormalizer._number(payload, *names)
        if value is None:
            return None
        if not value.is_integer() or value < 0:
            return None
        return int(value)

    def _price(self, payload: Mapping[str, Any], *names: str) -> float | None:
        value = self._number(payload, *names)
        return None if value is None else value / self.price_scale

    @staticmethod
    def _timestamp_ns(payload: Mapping[str, Any]) -> int:
        value = AngelOneTickNormalizer._number(
            payload, "exchange_timestamp_ns", "timestamp_ns"
        )
        if value is not None:
            return max(0, int(value))
        value = AngelOneTickNormalizer._number(
            payload, "exchange_timestamp", "exchange_timestamp_ms",
            "timestamp", "feed_time"
        )
        if value is None:
            return time_ns()
        # SmartAPI exchange timestamps are epoch milliseconds. Values already
        # in ns are retained to make the boundary tolerant of normalized feeds.
        integer = int(value)
        if integer >= 10**16:
            return integer
        return integer * 1_000_000

    @staticmethod
    def _depth_value(payload: Mapping[str, Any], side: str, field: str) -> Any:
        direct = payload.get(f"{side}_{field}")
        if direct is not None and AngelOneTickNormalizer._finite_float(direct) is not None:
            return direct
        for key in (f"best_5_{side}_data", f"best_{side}_data", f"best_{side}", side):
            value = payload.get(key)
            if isinstance(value, Mapping):
                for candidate in (field, f"{side}_{field}", "price" if field == "price" else "quantity", "qty" if field == "quantity" else field):
                    candidate_value = value.get(candidate)
                    if candidate_value is not None and AngelOneTickNormalizer._finite_float(candidate_value) is not None:
                        return candidate_value
            elif isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, Mapping):
                    for candidate in (field, "price" if field == "price" else "quantity", "qty"):
                        candidate_value = first.get(candidate)
                        if candidate_value is not None and AngelOneTickNormalizer._finite_float(candidate_value) is not None:
                            return candidate_value
        return None

    def normalize(self, descriptor: InstrumentDescriptor, payload: Mapping[str, Any]) -> MarketDataRecord:
        if not isinstance(descriptor, InstrumentDescriptor):
            raise TypeError("descriptor must be an InstrumentDescriptor")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        token = str(payload.get("token") or payload.get("symboltoken") or descriptor.key.token).strip()
        if token != descriptor.key.token.strip():
            raise ValueError("payload token does not match descriptor")
        instrument_type = self._TYPE_MAP.get(str(descriptor.instrument_type).strip().lower())
        if instrument_type is None:
            raise ValueError(f"unsupported instrument_type: {descriptor.instrument_type}")

        bid_raw = self._depth_value(payload, "buy", "price")
        ask_raw = self._depth_value(payload, "sell", "price")
        bid_qty_raw = self._depth_value(payload, "buy", "quantity")
        ask_qty_raw = self._depth_value(payload, "sell", "quantity")
        bid_value = self._finite_float(bid_raw)
        ask_value = self._finite_float(ask_raw)
        bid_qty_value = self._finite_float(bid_qty_raw)
        ask_qty_value = self._finite_float(ask_qty_raw)
        bid = None if bid_value is None or bid_value < 0 else bid_value / self.price_scale
        ask = None if ask_value is None or ask_value < 0 else ask_value / self.price_scale
        bid_qty = None if bid_qty_value is None or bid_qty_value < 0 else bid_qty_value
        ask_qty = None if ask_qty_value is None or ask_qty_value < 0 else ask_qty_value
        # A crossed/malformed depth snapshot should not discard otherwise valid
        # LTP/OHLC/OI fields from the same broker tick. Mark only the quote sides
        # unusable so downstream executable scanners can safely reject the quote.
        if bid is not None and ask is not None and bid > ask:
            bid = ask = None

        option_type = None
        if descriptor.option_type:
            value = str(descriptor.option_type).upper()
            option_type = OptionType(value)

        record = MarketDataRecord(
            instrument=InstrumentKey(
                descriptor.key.exchange, descriptor.key.segment, token
            ),
            symbol=descriptor.symbol,
            instrument_type=instrument_type,
            timestamp_ns=self._timestamp_ns(payload),
            timeframe="1s",
            ltp=self._price(payload, "last_traded_price", "ltp", "lastTradedPrice"),
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            volume=self._integer(payload, "volume_trade_for_the_day", "volume", "trade_volume"),
            oi=self._integer(payload, "open_interest", "oi"),
            open=self._price(payload, "open_price_of_the_day", "open"),
            high=self._price(payload, "high_price_of_the_day", "high"),
            low=self._price(payload, "low_price_of_the_day", "low"),
            close=self._price(payload, "closed_price", "close"),
            underlying=(str(payload.get("underlying") or "").strip().upper() or descriptor.underlying or descriptor.symbol),
            expiry=descriptor.expiry,
            strike=descriptor.strike,
            option_type=option_type,
            lot_size=descriptor.lot_size,
            tick_size=descriptor.tick_size,
            payload=dict(payload),
        )
        return record
