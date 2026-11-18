"""Isolated weighted-equity quote consumer for IV early-move research.

The basket and its official index weights are injected by the caller; this
module deliberately does not treat a liquidity ranking as index weights.
It owns only its IV consumer registration on the shared WebSocket manager.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from threading import RLock
from time import time_ns
from typing import Any, Callable, Iterable, Mapping

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentType
from app.market_data.registry import InstrumentDescriptor


@dataclass(frozen=True)
class WeightedEquity:
    symbol: str
    weight: float


@dataclass(frozen=True)
class EquityTick:
    symbol: str
    weight: float
    timestamp_ns: int
    ltp: float
    bid: float | None
    ask: float | None
    bid_qty: float | None
    ask_qty: float | None
    volume: int | None


class IVWeightedEquityFeedAdapter:
    """Freshness-checked NSE cash quotes, isolated from other strategy consumers."""

    CONSUMER = "iv-skew-flip-equities"

    def __init__(
        self,
        *,
        feed: Any | None = None,
        clock_ns: Callable[[], int] = time_ns,
        max_tick_age_seconds: float = 3.0,
        consumer: str | None = None,
    ) -> None:
        if max_tick_age_seconds <= 0:
            raise ValueError("max_tick_age_seconds must be positive")
        selected_consumer = str(consumer or self.CONSUMER).strip()
        if not selected_consumer or len(selected_consumer) > 64:
            raise ValueError("consumer must be a non-empty identifier of at most 64 characters")
        self.consumer = selected_consumer
        self.feed = feed if feed is not None else CommonStrategyMarketFeed(self.consumer, mode=1)
        self.clock_ns = clock_ns
        self.max_tick_age_ns = int(max_tick_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._by_key: dict[Any, WeightedEquity] = {}
        self._ticks: dict[str, EquityTick] = {}
        self._started = False

    @staticmethod
    def _number(value: Any, *, positive: bool = False) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not isfinite(result) or (positive and result <= 0):
            return None
        return result

    @staticmethod
    def _source_timestamp_ns(record: Any) -> int | None:
        """Return the exact provider timestamp after the normalizer's unit rules.

        The canonical record timestamp alone is insufficient: the normalizer
        falls back to local time when provider time is absent or malformed.
        Verify that the selected provider field maps exactly to that canonical
        timestamp so a different, lower-priority payload field cannot mask it.
        """
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping):
            return None
        for field in ("exchange_timestamp_ns", "timestamp_ns"):
            raw = payload.get(field)
            if raw is None:
                continue
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError, OverflowError):
                # The normalizer selects the first present field. Do not let a
                # malformed higher-priority field be masked by a lower one.
                return None
            return value if value > 0 else None
        raw = None
        for field in ("exchange_timestamp", "exchange_timestamp_ms", "timestamp", "feed_time"):
            if payload.get(field) is not None:
                raw = payload.get(field)
                break
        if raw is None or isinstance(raw, bool):
            return None
        try:
            text = str(raw).strip()
            try:
                value = int(text)
            except (TypeError, ValueError, OverflowError):
                number = float(raw)
                if not isfinite(number):
                    return None
                value = int(number)
        except (TypeError, ValueError, OverflowError):
            return None
        magnitude = abs(value)
        if magnitude >= 10**16:
            return value if value > 0 else None
        if magnitude >= 10**14:
            value *= 1_000
        elif magnitude >= 10**11:
            value *= 1_000_000
        else:
            value *= 1_000_000_000
        return value if value > 0 else None

    @classmethod
    def _source_timestamp_present(cls, record: Any) -> bool:
        source_ns = cls._source_timestamp_ns(record)
        canonical_ns = getattr(record, "timestamp_ns", None)
        return (
            source_ns is not None
            and isinstance(canonical_ns, int)
            and not isinstance(canonical_ns, bool)
            and source_ns == canonical_ns
        )

    def start(self, instrument_master: Any, basket: Iterable[WeightedEquity]) -> dict[str, Any]:
        items = tuple(basket)
        if not items:
            raise ValueError("weighted equity basket cannot be empty")
        normalized: list[tuple[WeightedEquity, Any]] = []
        symbols: set[str] = set()
        keys: set[Any] = set()
        for item in items:
            if not isinstance(item, WeightedEquity):
                raise TypeError("basket entries must be WeightedEquity")
            symbol = str(item.symbol).strip().upper()
            weight = self._number(item.weight, positive=True)
            if not symbol or weight is None:
                raise ValueError("each basket entry needs a symbol and positive finite weight")
            if symbol in symbols:
                raise ValueError(f"duplicate equity symbol: {symbol}")
            row = instrument_master.resolve_cash_instrument(symbol, "NSE")
            token = str(row.get("token", "")).strip()
            if not token:
                raise LookupError(f"missing NSE cash token for {symbol}")
            descriptor = CommonStrategyMarketFeed.descriptor(
                exchange="NSE", segment="NSE", token=token,
                symbol=str(row.get("symbol") or symbol),
                instrument_type=InstrumentType.EQUITY.value,
                underlying=symbol,
            )
            if descriptor.key in keys:
                raise LookupError(f"duplicate NSE cash token in basket: {token}")
            symbols.add(symbol)
            keys.add(descriptor.key)
            normalized.append((WeightedEquity(symbol, weight), descriptor))

        self.feed.stop()
        with self._lock:
            self._by_key = {descriptor.key: item for item, descriptor in normalized}
            self._ticks.clear()
        descriptors = tuple(descriptor for _, descriptor in normalized)
        try:
            self.feed.start(descriptors, self._on_record)
        except Exception:
            try:
                self.feed.stop()
            finally:
                with self._lock:
                    self._started = False
                    self._by_key.clear()
                    self._ticks.clear()
            raise
        with self._lock:
            self._started = True
        return {
            "status": "SUBSCRIBED",
            "consumer": self.consumer,
            "symbols": sorted(symbols),
            "contract_count": len(descriptors),
            "weight_sum": round(sum(item.weight for item, _ in normalized), 8),
        }

    def _on_record(self, record: Any) -> None:
        key = getattr(record, "instrument", None)
        timestamp = getattr(record, "timestamp_ns", None)
        if (not isinstance(timestamp, int) or isinstance(timestamp, bool)
                or timestamp <= 0 or not self._source_timestamp_present(record)):
            return
        ltp = self._number(getattr(record, "ltp", None), positive=True)
        if ltp is None:
            return
        with self._lock:
            item = self._by_key.get(key)
            if item is None:
                return
            old = self._ticks.get(item.symbol)
            if old is not None and timestamp <= old.timestamp_ns:
                return
            bid = self._number(getattr(record, "bid", None), positive=True)
            ask = self._number(getattr(record, "ask", None), positive=True)
            if bid is not None and ask is not None and bid > ask:
                bid = ask = None
            volume = getattr(record, "volume", None)
            if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
                volume = None
            self._ticks[item.symbol] = EquityTick(
                symbol=item.symbol, weight=item.weight, timestamp_ns=timestamp,
                ltp=ltp, bid=bid, ask=ask,
                bid_qty=self._number(getattr(record, "bid_qty", None), positive=True),
                ask_qty=self._number(getattr(record, "ask_qty", None), positive=True),
                volume=volume,
            )

    def snapshot(self, *, now_ns: int | None = None) -> dict[str, Any]:
        now = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            expected = {item.symbol: item for item in self._by_key.values()}
            ticks = dict(self._ticks)
            started = self._started
        fresh = {
            symbol: tick for symbol, tick in ticks.items()
            if 0 <= now - tick.timestamp_ns <= self.max_tick_age_ns
        }
        missing = sorted(set(expected) - set(fresh))
        if not started:
            status = "STOPPED"
        elif not missing:
            status = "LIVE"
        elif fresh:
            status = "PARTIAL"
        elif ticks:
            status = "STALE"
        else:
            status = "WAITING_FOR_TICKS"
        return {
            "status": status,
            "consumer": self.consumer,
            "expected_symbols": sorted(expected),
            "fresh_symbols": sorted(fresh),
            "missing_symbols": missing,
            "signals_allowed": bool(expected) and not missing,
            "quotes": {
                symbol: {
                    "weight": tick.weight, "ltp": tick.ltp,
                    "bid": tick.bid, "ask": tick.ask,
                    "bid_qty": tick.bid_qty, "ask_qty": tick.ask_qty,
                    "volume": tick.volume, "timestamp_ns": tick.timestamp_ns,
                    "age_ms": round((now - tick.timestamp_ns) / 1_000_000, 2),
                }
                for symbol, tick in fresh.items()
            },
        }

    def stop(self) -> None:
        self.feed.stop()
        with self._lock:
            self._started = False
            self._by_key.clear()
            self._ticks.clear()


__all__ = ["EquityTick", "IVWeightedEquityFeedAdapter", "WeightedEquity"]
