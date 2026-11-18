"""Provider-neutral broker contract.

Real order placement is deliberately broker-specific. Implementations must
never store raw broker passwords or TOTP secrets in this layer.
"""

from dataclasses import dataclass
from typing import Any, Protocol


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
    """Interface every supported broker must implement.

    Authentication credentials are supplied only at call time. Concrete
    adapters may keep an authenticated SDK client in process memory, but the
    provider-neutral layer never persists raw credentials or TOTP secrets.
    """

    name: str

    def connect(self, **credentials: Any) -> dict[str, Any]:
        """Authenticate with the broker and return safe connection metadata."""
        ...

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        """Place a real order only after application-level safety gates."""
        ...

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        """Cancel a real broker order only after application-level safety gates."""
        ...

    def disconnect(self) -> None:
        """Invalidate/close the broker session when supported."""
        ...
