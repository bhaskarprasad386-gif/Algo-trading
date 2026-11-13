"""Normalize merged Shoonya Noren touchline snapshots to canonical records."""
from __future__ import annotations
from typing import Any, Mapping
from .contracts import InstrumentKey, InstrumentType, MarketDataRecord, OptionType
from .registry import InstrumentDescriptor


class ShoonyaTickNormalizer:
    _TYPE_MAP = {"equity": InstrumentType.EQUITY, "index": InstrumentType.INDEX,
                 "future": InstrumentType.FUTURE, "option": InstrumentType.OPTION,
                 "commodity": InstrumentType.COMMODITY}

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or str(value).strip().upper() in {"", "NA", "NONE"}:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if result >= 0 and result != float("inf") and result == result else None

    @classmethod
    def _integer(cls, value: Any) -> int | None:
        number = cls._number(value)
        return int(number) if number is not None and number.is_integer() else None

    @staticmethod
    def _timestamp_ns(payload: Mapping[str, Any]) -> int:
        """Convert epoch seconds, milliseconds, microseconds, or nanoseconds to ns."""
        raw = payload.get("ft")
        if raw is None:
            return 0
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError, OverflowError):
            return 0
        if value <= 0:
            return 0
        if value >= 10**17:  # nanoseconds
            return value
        if value >= 10**14:  # microseconds
            return value * 1_000
        if value >= 10**11:  # milliseconds
            return value * 1_000_000
        return value * 1_000_000_000  # seconds

    def normalize(self, descriptor: InstrumentDescriptor, payload: Mapping[str, Any]) -> MarketDataRecord:
        if not isinstance(descriptor, InstrumentDescriptor) or not isinstance(payload, Mapping):
            raise TypeError("descriptor and payload have invalid types")
        token = str(payload.get("tk") or "").strip()
        exchange = str(payload.get("e") or "").strip().upper()
        if token != descriptor.key.token or exchange != descriptor.key.exchange.upper():
            raise ValueError("Shoonya tick identity does not match registered instrument")
        kind = self._TYPE_MAP.get(str(descriptor.instrument_type).strip().lower())
        if kind is None:
            raise ValueError(f"unsupported instrument_type: {descriptor.instrument_type}")
        option_type = OptionType(str(descriptor.option_type).upper()) if descriptor.option_type else None
        bid, ask = self._number(payload.get("bp1")), self._number(payload.get("sp1"))
        if bid is not None and ask is not None and bid > ask:
            bid = ask = None
        return MarketDataRecord(
            instrument=InstrumentKey(descriptor.key.exchange, descriptor.key.segment, token),
            symbol=str(payload.get("ts") or descriptor.symbol), instrument_type=kind,
            timestamp_ns=self._timestamp_ns(payload), timeframe="1s",
            ltp=self._number(payload.get("lp")), bid=bid, ask=ask,
            bid_qty=self._number(payload.get("bq1")), ask_qty=self._number(payload.get("sq1")),
            volume=self._integer(payload.get("v")), oi=self._integer(payload.get("oi")),
            open=self._number(payload.get("o")), high=self._number(payload.get("h")),
            low=self._number(payload.get("l")), close=self._number(payload.get("c")),
            underlying=descriptor.underlying or descriptor.symbol, expiry=descriptor.expiry,
            strike=descriptor.strike, option_type=option_type,
            lot_size=self._integer(payload.get("ls")) or descriptor.lot_size,
            tick_size=self._number(payload.get("ti")) or descriptor.tick_size,
            payload={**dict(payload), "broker": "shoonya", "price_precision": payload.get("pp")},
        )
