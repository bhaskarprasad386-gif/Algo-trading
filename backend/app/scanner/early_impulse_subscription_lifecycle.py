"""Bounded, isolated desired-subscription lifecycle for Early Impulse Pro.

This controller is intentionally dependency-injected. It is not yet wired to the
shared Shoonya supervisor because that supervisor's unsubscribe API owns adapter
subscriptions globally; token ownership/ref-count isolation must be integrated
before this controller can safely mutate the live socket.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable, Iterable

from app.market_data.registry import InstrumentDescriptor


ALLOWED_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"})
ALLOWED_EXCHANGES = frozenset({"NSE", "NFO", "BSE", "BFO"})
MAX_UNIVERSE = 150
MAX_OPTION_EXPIRIES_PER_UNDERLYING = 2


class EarlyImpulseSubscriptionLifecycle:
    """Validate and track a bounded scanner-only subscription set.

    Injected subscribe/unsubscribe functions should be consumer-scoped/ref-counted.
    No broker order or signal operation is performed here.
    """

    def __init__(
        self,
        *,
        subscribe: Callable[[tuple[InstrumentDescriptor, ...]], int] | None = None,
        unsubscribe: Callable[[tuple[InstrumentDescriptor, ...]], int] | None = None,
        max_universe: int = MAX_UNIVERSE,
    ) -> None:
        if isinstance(max_universe, bool) or not isinstance(max_universe, int) or not 1 <= max_universe <= MAX_UNIVERSE:
            raise ValueError(f"max_universe must be between 1 and {MAX_UNIVERSE}")
        self.max_universe = max_universe
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._lock = RLock()
        self._desired: dict[tuple[str, str, str], InstrumentDescriptor] = {}
        self._enabled = False

    @staticmethod
    def _key(item: InstrumentDescriptor) -> tuple[str, str, str]:
        return (item.key.exchange.strip().upper(), item.key.segment.strip().upper(), item.key.token.strip())

    @staticmethod
    def validate(items: Iterable[InstrumentDescriptor]) -> tuple[InstrumentDescriptor, ...]:
        batch = tuple(items)
        if not batch:
            raise ValueError("subscription universe cannot be empty")
        seen: dict[tuple[str, str, str], InstrumentDescriptor] = {}
        expiry_sets: dict[str, set[str]] = {}
        for item in batch:
            if not isinstance(item, InstrumentDescriptor):
                raise TypeError("all universe entries must be InstrumentDescriptor")
            exchange, segment, token = (str(item.key.exchange).strip().upper(), str(item.key.segment).strip().upper(), str(item.key.token).strip())
            if exchange not in ALLOWED_EXCHANGES or not segment or not token:
                raise ValueError("unsupported exchange/segment or empty token")
            if item.instrument_type not in {"index", "future", "option"}:
                raise ValueError("Early Impulse supports index, future, and option instruments only")
            underlying = (item.underlying or item.symbol).strip().upper()
            if underlying not in ALLOWED_UNDERLYINGS:
                raise ValueError("unsupported underlying")
            if item.instrument_type == "option":
                if item.expiry is None or not str(item.expiry).strip() or item.strike is None or item.strike <= 0 or item.option_type not in {"CE", "PE"}:
                    raise ValueError("option descriptor requires valid expiry, strike and CE/PE")
                expiry_sets.setdefault(underlying, set()).add(str(item.expiry).strip())
            elif item.option_type is not None or item.strike is not None:
                raise ValueError("strike and option_type are valid only for options")
            key = (exchange, segment, token)
            if key in seen:
                if seen[key] != item:
                    raise ValueError("conflicting token descriptors in requested universe")
                raise ValueError("duplicate token in requested universe")
            seen[key] = item
        if any(len(expiries) > MAX_OPTION_EXPIRIES_PER_UNDERLYING for expiries in expiry_sets.values()):
            raise ValueError(f"maximum {MAX_OPTION_EXPIRIES_PER_UNDERLYING} option expiries per underlying")
        return batch

    def set_universe(self, items: Iterable[InstrumentDescriptor]) -> dict:
        batch = self.validate(items)
        if len(batch) > self.max_universe:
            raise ValueError(f"universe cap exceeded ({self.max_universe})")
        staged = {self._key(item): item for item in batch}
        with self._lock:
            if self._enabled and self._subscribe is None:
                raise RuntimeError("subscription transport is not wired")
            old = self._desired
            added = tuple(item for key, item in staged.items() if key not in old)
            removed = tuple(item for key, item in old.items() if key not in staged)
            if self._enabled:
                if removed and self._unsubscribe is None:
                    raise RuntimeError("consumer-scoped unsubscribe transport is not wired")
                if added:
                    self._subscribe(added)
                try:
                    if removed:
                        self._unsubscribe(removed)
                except Exception:
                    if added and self._unsubscribe:
                        self._unsubscribe(added)
                    raise
            self._desired = staged
            return {"configured": len(staged), "added": len(added), "removed": len(removed), "enabled": self._enabled}

    def enable(self) -> dict:
        with self._lock:
            if self._enabled:
                return self.snapshot()
            if not self._desired:
                raise ValueError("configure an instrument universe before enabling")
            if self._subscribe is None:
                raise RuntimeError("subscription transport is not wired")
            self._subscribe(tuple(self._desired.values()))
            self._enabled = True
            return self.snapshot()

    def disable(self) -> dict:
        with self._lock:
            if not self._enabled:
                return self.snapshot()
            if self._unsubscribe is None:
                raise RuntimeError("consumer-scoped unsubscribe transport is not wired")
            self._unsubscribe(tuple(self._desired.values()))
            self._enabled = False
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "strategy": "EARLY_IMPULSE_PRO",
                "configured_instruments": len(self._desired),
                "max_universe": self.max_universe,
                "enabled": self._enabled,
                "transport_wired": self._subscribe is not None and self._unsubscribe is not None,
                "instrument_keys": sorted(f"{e}:{s}:{t}" for e, s, t in self._desired),
                "scanner_enabled": False,
                "signals_enabled": False,
                "order_routing_enabled": False,
            }
