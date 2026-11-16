"""Registry for supported broker adapters."""
from .base import BrokerAdapter
from .angel_one import AngelOneAdapter


class BrokerRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BrokerAdapter] = {}
        self.register(AngelOneAdapter())

    def register(self, adapter: BrokerAdapter) -> None:
        key = adapter.name.strip().lower()
        if not key:
            raise ValueError("broker adapter name cannot be empty")
        if key in self._adapters:
            raise ValueError(f"broker adapter already registered: {key}")
        self._adapters[key] = adapter

    def get(self, name: str) -> BrokerAdapter:
        key = name.strip().lower()
        if key not in self._adapters:
            raise KeyError(f"unsupported broker: {name}")
        return self._adapters[key]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
