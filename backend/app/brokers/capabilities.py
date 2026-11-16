"""Explicit broker capabilities for Batch 0.

Capabilities describe implemented, testable read-only surfaces. They do not
grant order mutation or Live-execution permission.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrokerCapability:
    broker: str
    display_name: str
    history_provider: bool
    history_window: str
    history_limitations: str
    connection_flow: str
    adapter_registered_for_routing: bool = False
    can_place_orders: bool = False
    can_cancel_orders: bool = False
    can_modify_orders: bool = False
    live_certified: bool = False


_CAPABILITIES = {
    "angel_one": BrokerCapability(
        broker="angel_one",
        display_name="Angel One",
        history_provider=True,
        history_window="CURRENT_IST_TRADING_DAY",
        history_limitations="SmartAPI orderBook/tradeBook only; both responses must succeed.",
        connection_flow="shared_owner_connect",
        adapter_registered_for_routing=True,
    ),
    "zerodha": BrokerCapability(
        broker="zerodha",
        display_name="Zerodha",
        history_provider=True,
        history_window="CURRENT_IST_TRADING_DAY",
        history_limitations="Kite order/trade books only; no multi-day history claim.",
        connection_flow="provider_specific",
    ),
    "fyers": BrokerCapability(
        broker="fyers",
        display_name="FYERS",
        history_provider=True,
        history_window="CLOSED_IST_CALENDAR_DAYS",
        history_limitations="Paginated date reports exclude current-day pending/open orders; separate orderbook evidence is required.",
        connection_flow="provider_specific",
    ),
    "shoonya": BrokerCapability(
        broker="shoonya",
        display_name="Shoonya",
        history_provider=True,
        history_window="CURRENT_IST_TRADING_DAY",
        history_limitations="Current-day Order Book and Trade Book only.",
        connection_flow="provider_specific",
    ),
    "mstock": BrokerCapability(
        broker="mstock",
        display_name="m.Stock",
        history_provider=True,
        history_window="CURRENT_IST_TRADING_DAY",
        history_limitations="Current-day Order Book and Trade Book only.",
        connection_flow="provider_specific",
    ),
}


class BrokerCapabilityRegistry:
    """Read-only capability catalogue, separate from executable adapter registry."""

    def __init__(self) -> None:
        self._capabilities = dict(_CAPABILITIES)

    def get(self, broker: str) -> BrokerCapability:
        key = str(broker).strip().lower()
        if not key:
            raise KeyError("broker name is required")
        try:
            return self._capabilities[key]
        except KeyError:
            raise KeyError(f"unsupported broker capability: {key}") from None

    def all(self) -> tuple[BrokerCapability, ...]:
        return tuple(self._capabilities[key] for key in sorted(self._capabilities))


broker_capabilities = BrokerCapabilityRegistry()
