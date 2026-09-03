"""Registry for supported broker adapters.

The registry keeps broker-specific code isolated from the trading engine so
new brokers can be added without changing scanner or execution logic.
"""

from .base import BrokerAdapter


class BrokerRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BrokerAdapter] = {}

    def register(self, adapter: BrokerAdapter) -> None:
        key = adapter.name.strip().lower()
        if not key:
            raise ValueError("broker adapter name cannot be empty")
        if key in self._adapters:
            raise ValueError(f"broker adapter already registered: {key}")
        self._adapters[key] = adapter

    def get(self, name: str) -> BrokerAdapter:
        key = name.strip().lower()
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise KeyError(f"unsupported broker: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
