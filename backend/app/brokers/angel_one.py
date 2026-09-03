from __future__ import annotations

from typing import Any

import pyotp
from SmartApi import SmartConnect


class AngelOneAdapter:
    """Angel One SmartAPI adapter.

    Credentials are accepted only at runtime. Session objects/tokens stay in
    process memory and are never returned by the API or persisted by this adapter.
    Live order routing remains disabled until the platform safety gate is enabled.
    """

    name = "angel_one"

    def __init__(self) -> None:
        self.smart_api: SmartConnect | None = None
        self.client_code: str | None = None
        self.connected = False

    def connect(self, **credentials: Any) -> dict:
        required = ("api_key", "client_code", "password", "totp_secret")
        missing = [key for key in required if not credentials.get(key)]
        if missing:
            raise ValueError(f"Missing Angel One authentication fields: {', '.join(missing)}")

        api_key = str(credentials["api_key"]).strip()
        client_code = str(credentials["client_code"]).strip()
        password = str(credentials["password"]).strip()
        totp_secret = str(credentials["totp_secret"]).strip()

        smart_api = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        response = smart_api.generateSession(client_code, password, totp)
        if not isinstance(response, dict) or not response.get("status"):
            message = "Angel One authentication failed"
            if isinstance(response, dict):
                message = str(response.get("message") or response.get("errorcode") or message)
            raise ValueError(message)

        self.smart_api = smart_api
        self.client_code = client_code
        self.connected = True
        return {"broker": self.name, "connected": True, "client_code": client_code}

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Live order routing is disabled until broker safety approval")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Live order routing is disabled until broker safety approval")

    def disconnect(self) -> None:
        if self.smart_api is not None:
            try:
                self.smart_api.terminateSession(self.client_code)
            except Exception:
                pass
        self.smart_api = None
        self.client_code = None
        self.connected = False
