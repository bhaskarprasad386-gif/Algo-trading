from __future__ import annotations

from typing import Any


class AngelOneAdapter:
    """Broker adapter boundary for Angel One SmartAPI.

    Credentials and session tokens are supplied at runtime by the authenticated
    connection flow and are never written to this module or the connection store.
    """

    name = "angel_one"

    def connect(self, **credentials: Any) -> dict:
        required = ("client_code", "password", "totp")
        missing = [key for key in required if not credentials.get(key)]
        if missing:
            raise ValueError("Missing Angel One authentication fields")
        return {"broker": self.name, "connected": True}

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Live order routing is disabled until broker safety approval")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Live order routing is disabled until broker safety approval")

    def disconnect(self) -> None:
        return None
