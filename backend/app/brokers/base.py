"""Provider-neutral broker contract.

Real order placement is deliberately broker-specific. Implementations must
never store raw broker passwords or TOTP secrets in this layer.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BrokerSession:
    broker: str
    user_id: str
    access_token: str


@dataclass(frozen=True)
class BrokerOrder:
    symbol: str
    exchange: str
    side: str
    quantity: int
    order_type: str = "MARKET"
    product: str = "INTRADAY"
    price: float | None = None


@dataclass(frozen=True)
class BrokerOrderResult:
    broker_order_id: str
    status: str
    message: str | None = None


class BrokerAdapter(Protocol):
    """Interface every supported broker must implement."""

    name: str

    def connect(self, credentials: dict[str, str]) -> BrokerSession:
        """Authenticate with the broker and return a short-lived session."""
        ...

    def place_order(self, session: BrokerSession, order: BrokerOrder) -> BrokerOrderResult:
        """Place one real order after application-level safety gates."""
        ...

    def cancel_order(self, session: BrokerSession, broker_order_id: str) -> BrokerOrderResult:
        """Cancel an existing broker order."""
        ...

    def disconnect(self, session: BrokerSession) -> None:
        """Invalidate/close the broker session when supported."""
        ...
