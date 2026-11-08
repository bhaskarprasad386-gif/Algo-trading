"""IV-owned live NFO index-futures quote consumer."""
from __future__ import annotations

from math import isfinite
from threading import RLock
from time import time_ns
from typing import Any, Callable, Iterable

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from .basis import IndexFutureContract


def _provider_timestamp_ns(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("exchange_timestamp_ns", "timestamp_ns", "exchange_timestamp", "exchange_timestamp_ms", "timestamp", "feed_time"):
        value = payload.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not isfinite(number) or number <= 0:
            continue
        if number < 100_000_000_000:
            return int(number * 1_000_000_000)
        if number < 100_000_000_000_000:
            return int(number * 1_000_000)
        if number < 100_000_000_000_000_000:
            return int(number * 1_000)
        return int(number)
    return None


class IVIndexFutureFeedAdapter:
    CONSUMER = "iv-skew-flip-index-futures"

    def __init__(
        self, *, feed: Any | None = None, clock_ns: Callable[[], int] = time_ns,
        max_quote_age_seconds: float = 3.0,
    ) -> None:
        if max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be positive")
        self.feed = feed or CommonStrategyMarketFeed(self.CONSUMER, mode=1)
        self.clock_ns = clock_ns
        self.max_age_ns = int(max_quote_age_seconds * 1_000_000_000)
        self._lock = RLock()
        self._contracts: dict[str, IndexFutureContract] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._started = False

    def start(self, contracts: Iterable[IndexFutureContract]) -> dict[str, Any]:
        items = tuple(contracts)
        if not items:
            raise ValueError("at least one index future contract is required")
        tokens = [item.token for item in items]
        if len(tokens) != len(set(tokens)):
            raise ValueError("duplicate future token in IV subscription")
        descriptors = tuple(CommonStrategyMarketFeed.descriptor(
            exchange=item.exchange, segment=item.exchange, token=item.token,
            symbol=item.symbol, instrument_type="future",
            expiry=item.expiry.isoformat(), underlying=item.underlying,
        ) for item in items)
        self.feed.stop()
        with self._lock:
            self._contracts = {item.token: item for item in items}
            self._latest.clear()
        self.feed.start(descriptors, self._on_record)
        self._started = True
        return {
            "status": "SUBSCRIBED", "consumer": self.CONSUMER,
            "contract_count": len(items),
            "contracts": [{"underlying": item.underlying, "symbol": item.symbol,
                           "token": item.token, "expiry": item.expiry.isoformat()} for item in items],
        }

    def _on_record(self, record: Any) -> None:
        token = str(getattr(getattr(record, "instrument", None), "token", "") or "").strip()
        timestamp_ns = _provider_timestamp_ns(getattr(record, "payload", None))
        price = getattr(record, "ltp", None)
        if (
            not token or timestamp_ns is None
            or isinstance(price, bool) or not isinstance(price, (int, float))
            or not isfinite(float(price)) or price <= 0
        ):
            return
        with self._lock:
            if token not in self._contracts:
                return
            previous = self._latest.get(token)
            if previous and timestamp_ns <= previous["timestamp_ns"]:
                return
            self._latest[token] = {"price": float(price), "timestamp_ns": timestamp_ns}

    def snapshot(self, *, now_ns: int | None = None) -> dict[str, Any]:
        now = self.clock_ns() if now_ns is None else now_ns
        with self._lock:
            contracts = dict(self._contracts)
            latest = dict(self._latest)
            started = self._started
        quotes = {}
        missing = []
        for token, contract in contracts.items():
            quote = latest.get(token)
            age = None if quote is None else now - quote["timestamp_ns"]
            fresh = quote is not None and age is not None and 0 <= age <= self.max_age_ns
            if quote is None or not fresh:
                missing.append(token)
            quotes[token] = {
                "underlying": contract.underlying, "symbol": contract.symbol,
                "token": token, "expiry": contract.expiry.isoformat(),
                "price": None if quote is None else quote["price"],
                "timestamp_ns": None if quote is None else quote["timestamp_ns"],
                "age_ms": None if age is None else round(age / 1_000_000, 2),
                "fresh": fresh,
                "quote_status": "LIVE" if fresh else ("STALE" if quote else "WAITING_FOR_TICK"),
            }
        fresh_count = len(contracts) - len(missing)
        status = "STOPPED" if not started else "LIVE" if contracts and not missing else "PARTIAL" if fresh_count else "STALE" if latest else "WAITING_FOR_TICKS"
        return {
            "status": status, "consumer": self.CONSUMER,
            "expected_contracts": len(contracts), "fresh_contracts": fresh_count,
            "missing_tokens": sorted(missing), "quotes": quotes,
            "signals_allowed": bool(contracts) and not missing,
        }

    def stop(self) -> None:
        self.feed.stop()
        with self._lock:
            self._started = False
            self._contracts.clear()
            self._latest.clear()


__all__ = ["IVIndexFutureFeedAdapter"]
