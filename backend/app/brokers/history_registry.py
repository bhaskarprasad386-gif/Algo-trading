"""Composition registry for the five read-only broker history providers.

This registry is intentionally separate from BrokerRegistry: a history provider
can be composed for reconciliation without registering an executable order
adapter or granting any broker mutation capability.
"""
from __future__ import annotations

from typing import Any, Callable

from app.brokers.angel_one_history import AngelOneHistoryProvider
from app.brokers.fyers_history import FyersHistoryProvider
from app.brokers.history import BrokerHistorySnapshotProvider
from app.brokers.mstock_history import MStockHistoryProvider
from app.brokers.shoonya_history import ShoonyaHistoryProvider
from app.brokers.zerodha_history import ZerodhaHistoryProvider


class BrokerHistoryProviderRegistry:
    """Build a provider only when its broker identity matches the session adapter."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[Any], BrokerHistorySnapshotProvider]] = {
            "angel_one": AngelOneHistoryProvider,
            "zerodha": ZerodhaHistoryProvider,
            "fyers": FyersHistoryProvider,
            "shoonya": ShoonyaHistoryProvider,
            "mstock": MStockHistoryProvider,
        }

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def build(self, broker: str, adapter: Any) -> BrokerHistorySnapshotProvider:
        key = str(broker).strip().lower()
        if not key:
            raise KeyError("broker name is required")
        factory = self._factories.get(key)
        if factory is None:
            raise KeyError(f"unsupported broker history provider: {key}")
        adapter_name = str(getattr(adapter, "name", "")).strip().lower()
        if adapter_name != key:
            raise ValueError("broker history provider/session identity mismatch")
        provider = factory(adapter)
        if str(getattr(provider, "name", "")).strip().lower() != key:
            raise ValueError("broker history provider factory returned a mismatched provider")
        if not callable(getattr(provider, "fetch_order_trade_snapshot", None)):
            raise TypeError("broker history provider does not implement the read-only contract")
        return provider


broker_history_providers = BrokerHistoryProviderRegistry()
