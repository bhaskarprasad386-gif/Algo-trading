"""Broker-neutral contract for live market-data feed adapters."""
from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable


@runtime_checkable
class LiveMarketDataFeed(Protocol):
    """Lifecycle contract shared by broker-specific live feed adapters.

    Strategies consume canonical records through injected callbacks. They do
    not depend on a broker SDK, token format, or WebSocket implementation.
    """

    def start(self) -> None: ...
    def subscribe(self, descriptors: Iterable[Any]) -> int: ...
    def unsubscribe(self, descriptors: Iterable[Any]) -> int: ...
    def stop(self) -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
