"""Shared instrument and subscription registry for the common market-data layer."""
from __future__ import annotations
from dataclasses import dataclass
from threading import RLock
from typing import Iterable
from .contracts import InstrumentKey

@dataclass(frozen=True)
class InstrumentDescriptor:
    key: InstrumentKey
    symbol: str
    instrument_type: str
    exchange: str
    segment: str
    expiry: str | None = None
    strike: float | None = None
    option_type: str | None = None
    lot_size: int | None = None
    tick_size: float | None = None

@dataclass(frozen=True)
class Subscription:
    key: InstrumentKey
    consumers: frozenset[str]
    mode: int = 1
    @property
    def ref_count(self) -> int: return len(self.consumers)

class InstrumentRegistry:
    """Thread-safe registry that deduplicates instruments and subscriptions."""
    def __init__(self) -> None:
        self._lock=RLock(); self._instruments={}; self._consumers={}; self._modes={}
    def register(self, descriptor: InstrumentDescriptor) -> InstrumentKey:
        if not isinstance(descriptor, InstrumentDescriptor): raise TypeError("descriptor must be an InstrumentDescriptor")
        with self._lock:
            old=self._instruments.get(descriptor.key)
            if old is not None and old != descriptor: raise ValueError(f"conflicting descriptor for {descriptor.key.value}")
            self._instruments[descriptor.key]=descriptor
        return descriptor.key
    def register_many(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        n=0
        for d in descriptors: self.register(d); n+=1
        return n
    def subscribe(self, consumer: str, key: InstrumentKey, mode: int=1) -> Subscription:
        if not str(consumer).strip(): raise ValueError("consumer is required")
        if isinstance(mode,bool) or not isinstance(mode,int) or mode not in {1,2,3,4}: raise ValueError("mode must be one of 1, 2, 3 or 4")
        with self._lock:
            if key not in self._instruments: raise KeyError(f"instrument is not registered: {key.value}")
            self._consumers.setdefault(key,set()).add(consumer.strip()); self._modes[key]=mode
            return self._subscription_locked(key)
    def unsubscribe(self, consumer: str, key: InstrumentKey) -> Subscription|None:
        with self._lock:
            consumers=self._consumers.get(key)
            if consumers is None: return None
            consumers.discard(str(consumer).strip())
            if not consumers:
                self._consumers.pop(key,None); self._modes.pop(key,None); return None
            return self._subscription_locked(key)
    def _subscription_locked(self,key): return Subscription(key,frozenset(self._consumers.get(key,set())),self._modes.get(key,1))
    def get(self,key): 
        with self._lock: return self._instruments.get(key)
    def subscriptions(self):
        with self._lock: return tuple(self._subscription_locked(k) for k in sorted(self._consumers,key=lambda x:x.value))
    def active_keys(self):
        with self._lock: return tuple(sorted(self._consumers,key=lambda x:x.value))
    def broker_tokens(self, exchange: str|None=None):
        with self._lock:
            groups={}
            for k in self._consumers:
                if exchange and k.exchange.strip().upper()!=exchange.strip().upper(): continue
                groups.setdefault(f"{k.exchange.strip()}:{k.segment.strip()}",[]).append(k.token.strip())
            return {g:tuple(sorted(set(v),key=lambda x:(len(x),x))) for g,v in sorted(groups.items())}
    def clear_consumer(self, consumer: str) -> int:
        consumer=str(consumer).strip(); removed=0
        with self._lock:
            for k in list(self._consumers):
                c=self._consumers[k]
                if consumer in c:
                    c.remove(consumer); removed+=1
                    if not c: self._consumers.pop(k); self._modes.pop(k,None)
        return removed
