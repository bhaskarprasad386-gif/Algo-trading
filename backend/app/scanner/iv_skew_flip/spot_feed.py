"""Independent live index-spot consumer for the IV Skew / IV Flip scanner.

Uses the process-wide CommonStrategyMarketFeed; it never owns a broker socket
and never borrows Box Spread's tracker or subscription lifecycle.
"""
from __future__ import annotations

from math import isfinite
from threading import RLock
from time import time_ns
from typing import Any, Callable, Iterable

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.contracts import InstrumentType
from app.market_data.registry import InstrumentDescriptor

from .sampler import SpotSnapshot


class IVSpotFeedAdapter:
    CONSUMER = "iv-skew-flip-spot"
    DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY")

    def __init__(
        self,
        *,
        feed: Any | None = None,
        clock_ns: Callable[[], int] = time_ns,
        max_spot_age_seconds: float = 3.0,
    ) -> None:
        if max_spot_age_seconds <= 0:
            raise ValueError("max_spot_age_seconds must be positive")
        self.feed = feed or CommonStrategyMarketFeed(self.CONSUMER, mode=1)
        self.clock_ns = clock_ns
        self.max_spot_age_ns = int(max_spot_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._keys: dict[str, Any] = {}
        self._latest: dict[str, SpotSnapshot] = {}
        self._started = False

    @staticmethod
    def _descriptors(instrument_master: Any, symbols: Iterable[str]) -> tuple[InstrumentDescriptor, ...]:
        descriptors = []
        seen_tokens: set[str] = set()
        for raw_symbol in symbols:
            symbol = str(raw_symbol or "").strip().upper()
            if symbol not in IVSpotFeedAdapter.DEFAULT_SYMBOLS:
                raise ValueError(f"unsupported IV scanner spot symbol: {symbol}")
            row = instrument_master.resolve_index_instrument(symbol, "NSE")
            token = str(row.get("token", "")).strip()
            if not token or token in seen_tokens:
                raise LookupError(f"missing or duplicate NSE index token for {symbol}")
            seen_tokens.add(token)
            trading_symbol = str(row.get("symbol") or row.get("name") or symbol).strip()
            descriptors.append(CommonStrategyMarketFeed.descriptor(
                exchange="NSE",
                segment="NSE",
                token=token,
                symbol=trading_symbol,
                instrument_type=InstrumentType.INDEX.value,
                underlying=symbol,
            ))
        if not descriptors:
            raise ValueError("at least one IV scanner spot symbol is required")
        return tuple(descriptors)

    def start(self, instrument_master: Any, symbols: Iterable[str] = DEFAULT_SYMBOLS) -> dict[str, Any]:
        descriptors = self._descriptors(instrument_master, symbols)
        # This clears only the IV spot consumer's previous registration.
        self.feed.stop()
        with self._lock:
            self._keys = {
                str(item.underlying).upper(): item.key
                for item in descriptors
            }
            self._latest.clear()
        keys = self.feed.start(descriptors, self._on_record)
        self._started = True
        return {
            "status": "SUBSCRIBED",
            "consumer": self.CONSUMER,
            "symbols": sorted(self._keys),
            "contract_count": len(keys),
        }

    @staticmethod
    def _has_source_timestamp(record: Any) -> bool:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            return False
        for name in (
            "exchange_timestamp_ns", "timestamp_ns", "exchange_timestamp",
            "exchange_timestamp_ms", "timestamp", "feed_time",
        ):
            value = payload.get(name)
            if value is None or isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if isfinite(number) and number > 0:
                return True
        return False

    def _on_record(self, record: Any) -> None:
        instrument = getattr(record, "instrument", None)
        timestamp_ns = getattr(record, "timestamp_ns", None)
        price = getattr(record, "ltp", None)
        instrument_type = getattr(getattr(record, "instrument_type", None), "value", getattr(record, "instrument_type", None))
        if instrument_type != InstrumentType.INDEX.value or not self._has_source_timestamp(record):
            return
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns <= 0:
            return
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            return
        with self._lock:
            symbol = next((name for name, key in self._keys.items() if key == instrument), None)
            if symbol is None:
                return
            previous = self._latest.get(symbol)
            if previous is not None and timestamp_ns <= previous.timestamp_ns:
                return
            self._latest[symbol] = SpotSnapshot(float(price), timestamp_ns)

    def spot_snapshot(self, symbol: str, *, now_ns: int | None = None) -> SpotSnapshot | None:
        current = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            snapshot = self._latest.get(str(symbol or "").strip().upper())
        if snapshot is None:
            return None
        age = current - snapshot.timestamp_ns
        if age < 0 or age > self.max_spot_age_ns:
            return None
        return snapshot

    def snapshot(self, *, now_ns: int | None = None) -> dict[str, Any]:
        current = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            expected = tuple(self._keys)
            latest = dict(self._latest)
            started = self._started
        fresh = {
            symbol: value for symbol, value in latest.items()
            if 0 <= current - value.timestamp_ns <= self.max_spot_age_ns
        }
        missing = sorted(set(expected) - set(fresh))
        if not started:
            status = "STOPPED"
        elif not missing:
            status = "LIVE"
        elif fresh:
            status = "PARTIAL"
        elif latest:
            status = "STALE"
        else:
            status = "WAITING_FOR_TICKS"
        return {
            "status": status,
            "consumer": self.CONSUMER,
            "expected_symbols": sorted(expected),
            "fresh_symbols": sorted(fresh),
            "missing_symbols": missing,
            "spot": {
                symbol: {"price": item.price, "timestamp_ns": item.timestamp_ns}
                for symbol, item in fresh.items()
            },
            "signals_allowed": bool(expected) and not missing,
        }

    def stop(self) -> None:
        self.feed.stop()
        with self._lock:
            self._started = False
            self._latest.clear()
            self._keys.clear()


__all__ = ["IVSpotFeedAdapter"]
