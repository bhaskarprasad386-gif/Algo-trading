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
    def _timestamp_detail(payload: Mapping[str, Any]) -> tuple[int, str]:
        """Return normalized timestamp plus provenance, without retaining raw values."""
        ns_names = ("exchange_timestamp_ns", "timestamp_ns")
        ms_names = ("exchange_timestamp", "exchange_timestamp_ms", "timestamp", "feed_time")
        saw_timestamp_field = any(
            name in payload and payload.get(name) is not None and str(payload.get(name)).strip() != ""
            for name in (*ns_names, *ms_names)
        )
        value = AngelOneTickNormalizer._number(payload, *ns_names)
        if value is not None:
            if value <= 0:
                return max(0, int(value)), "invalid"
            return int(value), "broker_ns"
        value = AngelOneTickNormalizer._number(payload, *ms_names)
        if value is not None:
            integer = int(value)
            if integer <= 0:
                return 0, "invalid"
            # SmartAPI exchange timestamps are epoch milliseconds. Values already
            # in ns are retained to tolerate normalized upstream feeds.
            if integer >= 10**16:
                return integer, "broker_ns"
            return integer * 1_000_000, "broker_ms"
        if saw_timestamp_field:
            return time_ns(), "fallback_invalid"
        return time_ns(), "fallback_missing"

    @staticmethod
    def _timestamp_ns(payload: Mapping[str, Any]) -> int:
        # Compatibility wrapper for existing callers/tests.
        return AngelOneTickNormalizer._timestamp_detail(payload)[0]

    @classmethod
    def _depth_detail(
        cls, payload: Mapping[str, Any], side: str, price_scale: float = 100.0
    ) -> tuple[Any, Any, bool]:
        """Return the first usable positive price, quantity, and unit marker.

        Broker depth arrays can contain empty or malformed top levels. Skip those
        levels rather than turning an otherwise valid side into a missing quote.
        Direct bid/ask aliases may be rupees or broker-scaled integers; when LTP is
        present, compare magnitudes against normalized LTP to distinguish them.
        """
        ltp_raw = cls._number(payload, "last_traded_price", "ltp", "lastTradedPrice")
        ltp_rupees = ltp_raw / price_scale if ltp_raw is not None and ltp_raw > 0 else None
        direct_names = (
            f"{side}_price",
            "bid" if side == "buy" else "ask",
        )
        for direct_name in direct_names:
            direct_price = cls._finite_float(payload.get(direct_name))
            if direct_price is not None and direct_price > 0:
                direct_qty = cls._finite_float(
                    payload.get(f"{side}_quantity")
                    if payload.get(f"{side}_quantity") is not None
                    else payload.get("bid_qty" if side == "buy" else "ask_qty")
                )
                is_bid_ask_alias = direct_name == ("bid" if side == "buy" else "ask")
                # Without a usable LTP, the semantic bid/ask alias is treated as
                # canonical rupees. With LTP, values ~100x LTP are broker-scaled.
                is_canonical = is_bid_ask_alias and (
                    ltp_rupees is None
                    or ltp_rupees / 10.0 <= direct_price <= ltp_rupees * 10.0
                )
                return direct_price, direct_qty, is_canonical

        for key in (f"best_5_{side}_data", f"best_{side}_data", f"best_{side}", side):
            value = payload.get(key)
            levels = [value] if isinstance(value, Mapping) else value if isinstance(value, list) else ()
            for level in levels:
                if not isinstance(level, Mapping):
                    continue
                raw_price = None
                for candidate in ("price", f"{side}_price"):
                    candidate_value = cls._finite_float(level.get(candidate))
                    if candidate_value is not None and candidate_value > 0:
                        raw_price = level.get(candidate)
                        break
                if raw_price is None:
                    continue
                raw_quantity = None
                for candidate in ("quantity", "qty", f"{side}_quantity"):
                    candidate_value = cls._finite_float(level.get(candidate))
                    if candidate_value is not None:
                        raw_quantity = level.get(candidate)
                        break
                return raw_price, raw_quantity, False
        return None, None, False

    def normalize(
        self,
        descriptor: InstrumentDescriptor,
        payload: Mapping[str, Any],
        *,
        received_at_ns: int | None = None,
    ) -> MarketDataRecord:
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

        bid_raw, bid_qty_raw, bid_is_canonical = self._depth_detail(payload, "buy", self.price_scale)
        ask_raw, ask_qty_raw, ask_is_canonical = self._depth_detail(payload, "sell", self.price_scale)
        bid_value = self._finite_float(bid_raw)
        ask_value = self._finite_float(ask_raw)
        bid_qty_value = self._finite_float(bid_qty_raw)
        ask_qty_value = self._finite_float(ask_qty_raw)
        bid = None if bid_value is None or bid_value < 0 else bid_value / (1.0 if bid_is_canonical else self.price_scale)
        ask = None if ask_value is None or ask_value < 0 else ask_value / (1.0 if ask_is_canonical else self.price_scale)
        bid_qty = None if bid_qty_value is None or bid_qty_value < 0 else bid_qty_value
        ask_qty = None if ask_qty_value is None or ask_qty_value < 0 else ask_qty_value
        # Preserve a bounded reason code before clearing unusable crossed quotes.
        # This distinguishes a genuinely absent ask from a crossed book that was
        # intentionally invalidated; never retain the raw quote values in diagnostics.
        quote_normalization_reason = (
            "crossed_book" if bid is not None and ask is not None and bid > ask
            else "missing_both_sides" if bid is None and ask is None
            else "missing_bid" if bid is None
            else "missing_ask" if ask is None
            else "quotes_valid"
        )
        if quote_normalization_reason == "crossed_book":
            bid = ask = None

        option_type = None
        if descriptor.option_type:
            value = str(descriptor.option_type).upper()
            option_type = OptionType(value)

        timestamp_ns, timestamp_provenance = self._timestamp_detail(payload)
        normalized_payload = dict(payload)
        normalized_payload["_timestamp_provenance"] = timestamp_provenance
        normalized_payload["_quote_normalization_reason"] = quote_normalization_reason
        if received_at_ns is not None and int(received_at_ns) > 0:
            normalized_payload["_received_at_ns"] = int(received_at_ns)
        record = MarketDataRecord(
            instrument=InstrumentKey(
                descriptor.key.exchange, descriptor.key.segment, token
            ),
            symbol=descriptor.symbol,
            instrument_type=instrument_type,
            timestamp_ns=timestamp_ns,
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
            payload=normalized_payload,
        )
        return record
