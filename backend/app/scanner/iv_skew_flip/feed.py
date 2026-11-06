"""Isolated option-quote consumer for the IV Skew / IV Flip scanner.

This adapter subscribes through the existing CommonStrategyMarketFeed. It owns
only the iv-skew-flip consumer and never opens a broker socket or places orders.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.registry import InstrumentDescriptor

from .engine import OptionQuote
from .universe import ResolvedOptionUniverse


class IVSkewFlipFeedAdapter:
    """Subscribe the resolved option chain and retain the latest normalized quotes."""

    CONSUMER = "iv-skew-flip"
    MAX_QUOTE_AGE_NS = 5_000_000_000

    def __init__(
        self,
        *,
        feed: Any | None = None,
        clock_ns: Callable[[], int] | None = None,
        consumer: str | None = None,
    ) -> None:
        self.consumer = str(consumer or self.CONSUMER).strip()
        if not self.consumer:
            raise ValueError("consumer is required")
        self.feed = feed or CommonStrategyMarketFeed(self.consumer, mode=3)
        self._clock_ns = clock_ns
        self._lock = RLock()
        self._universe: ResolvedOptionUniverse | None = None
        self._quotes: dict[tuple[float, str], OptionQuote] = {}
        self._tokens: dict[str, tuple[float, str]] = {}
        self._last_tick_ns: int | None = None
        self._started = False

    @staticmethod
    def _descriptor(universe: ResolvedOptionUniverse, contract: Any) -> InstrumentDescriptor:
        return CommonStrategyMarketFeed.descriptor(
            exchange=contract.exchange,
            segment=contract.exchange,
            token=contract.token,
            symbol=contract.symbol,
            instrument_type="option",
            expiry=universe.expiry,
            strike=contract.strike,
            option_type=contract.option_type,
            lot_size=contract.lot_size,
            underlying=universe.underlying,
        )

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        from math import isfinite
        return result if isfinite(result) else None

    @staticmethod
    def _has_provider_timestamp(record: Any) -> bool:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            return False
        for name in (
            "exchange_timestamp_ns", "timestamp_ns", "exchange_timestamp",
            "exchange_timestamp_ms", "timestamp", "feed_time",
        ):
            value = payload.get(name)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if number > 0:
                return True
        return False

    def _on_record(self, record: Any) -> None:
        option_type = getattr(getattr(record, "option_type", None), "value", getattr(record, "option_type", None))
        option_type = str(option_type or "").strip().upper()
        strike = self._number(getattr(record, "strike", None))
        timestamp_ns = getattr(record, "timestamp_ns", None)
        token = str(getattr(getattr(record, "instrument", None), "token", "") or "").strip()
        if option_type not in {"CE", "PE"} or strike is None or not isinstance(timestamp_ns, int) or not self._has_provider_timestamp(record):
            return
        with self._lock:
            universe = self._universe
            if universe is None or self._tokens.get(token) != (strike, option_type):
                return
            previous = self._quotes.get((strike, option_type))
            if previous is not None and timestamp_ns <= previous.timestamp_ns:
                return
            quote = OptionQuote(
                underlying=universe.underlying,
                expiry=universe.expiry,
                strike=strike,
                option_type=option_type,
                timestamp_ns=timestamp_ns,
                ltp=self._number(getattr(record, "ltp", None)),
                bid=self._number(getattr(record, "bid", None)),
                ask=self._number(getattr(record, "ask", None)),
                oi=getattr(record, "oi", None),
                volume=getattr(record, "volume", None),
                token=token,
            )
            self._quotes[(strike, option_type)] = quote
            self._last_tick_ns = timestamp_ns

    def start(self, universe: ResolvedOptionUniverse) -> dict[str, Any]:
        """Atomically hand this consumer to a new exact-expiry option universe."""
        if not isinstance(universe, ResolvedOptionUniverse):
            raise TypeError("universe must be a ResolvedOptionUniverse")
        descriptors = tuple(self._descriptor(universe, item) for item in universe.contracts)
        if len(descriptors) != 14:
            raise ValueError("IV scanner requires exactly 14 contracts (7 strikes x CE/PE)")
        # Clear only this consumer's old tokens; other scanner consumers retain
        # shared subscriptions and the common socket remains owned by the manager.
        self.feed.stop()
        with self._lock:
            self._universe = universe
            self._quotes.clear()
            self._tokens = {
                contract.token: (contract.strike, contract.option_type)
                for contract in universe.contracts
            }
            self._last_tick_ns = None
        keys = self.feed.start(descriptors, self._on_record)
        self._started = True
        return {
            "status": "SUBSCRIBED",
            "underlying": universe.underlying,
            "expiry_type": universe.expiry_type,
            "expiry": universe.expiry,
            "strike_count": len(universe.strikes),
            "contract_count": len(keys),
            "consumer": self.consumer,
        }

    def stop(self) -> None:
        """Release only the IV scanner's shared-feed consumer registration."""
        self.feed.stop()
        with self._lock:
            self._started = False
            self._quotes.clear()
            self._tokens.clear()
            self._last_tick_ns = None

    def latest_quotes(self) -> tuple[OptionQuote, ...]:
        with self._lock:
            return tuple(sorted(self._quotes.values(), key=lambda q: (q.strike, q.option_type)))

    def snapshot(self, now_ns: int | None = None) -> dict[str, Any]:
        if now_ns is None:
            if self._clock_ns is None:
                import time
                now_ns = time.time_ns()
            else:
                now_ns = self._clock_ns()
        with self._lock:
            quotes = tuple(self._quotes.values())
            universe = self._universe
            started = self._started
            last_tick = self._last_tick_ns
        fresh = sum(
            1 for quote in quotes
            if 0 <= now_ns - quote.timestamp_ns <= self.MAX_QUOTE_AGE_NS
        )
        stale = len(quotes) - fresh
        expected = 0 if universe is None else len(universe.contracts)
        if not started:
            status = "STOPPED"
        elif fresh == expected and expected:
            status = "LIVE"
        elif fresh:
            status = "PARTIAL"
        elif quotes:
            status = "STALE"
        else:
            status = "WAITING_FOR_TICKS"
        return {
            "status": status,
            "consumer": self.consumer,
            "underlying": None if universe is None else universe.underlying,
            "expiry": None if universe is None else universe.expiry,
            "expected_contracts": expected,
            "received_contracts": len(quotes),
            "fresh_contracts": fresh,
            "stale_contracts": stale,
            "last_tick_timestamp_ns": last_tick,
            "signals_allowed": bool(expected and fresh == expected),
        }


__all__ = ["IVSkewFlipFeedAdapter"]
