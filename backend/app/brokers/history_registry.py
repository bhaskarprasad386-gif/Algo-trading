"""Registry for authenticated, read-only broker history capabilities.

This registry is deliberately separate from BrokerRegistry: registering a
history provider never makes a broker eligible for order placement, modify,
or cancellation. Provider instances must already be bound to an authenticated
single-account session by their caller.
"""
from __future__ import annotations

from app.brokers.history import BrokerHistorySnapshotProvider


class BrokerHistoryProviderRegistry:
    """Resolve read-only history providers without exposing order mutations."""

    def __init__(self) -> None:
        self._providers: dict[str, BrokerHistorySnapshotProvider] = {}

    def register(self, provider: BrokerHistorySnapshotProvider) -> None:
        name = str(getattr(provider, "name", "") or "").strip().lower()
        fetch = getattr(provider, "fetch_order_trade_snapshot", None)
        if not name:
            raise ValueError("broker history provider name cannot be empty")
        if not callable(fetch):
            raise TypeError("broker history provider must implement fetch_order_trade_snapshot")
        if name in self._providers:
            raise ValueError(f"broker history provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> BrokerHistorySnapshotProvider:
        key = str(name or "").strip().lower()
        if key not in self._providers:
            raise KeyError(f"unsupported broker history provider: {name}")
        return self._providers[key]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))
