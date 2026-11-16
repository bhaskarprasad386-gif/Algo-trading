from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import requests


class ZerodhaAuthenticationError(RuntimeError):
    """Raised when the documented Kite Connect login exchange is rejected."""


@dataclass(frozen=True)
class ZerodhaAuthenticatedSession:
    """Short-lived authenticated identity and access token returned by Kite."""

    account_id: str
    access_token: str


class ZerodhaTypeAAuthenticator:
    """Kite Connect request-token exchange with broker-side identity verification.

    The API secret and request token are request-scoped and never retained.
    This helper performs authentication only; it does not place or cancel orders.
    """

    base_url = "https://api.kite.trade"

    def __init__(self, http: Any | None = None) -> None:
        self.http = http or requests.Session()

    @staticmethod
    def _data(response: Any, endpoint: str) -> dict[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ZerodhaAuthenticationError(
                f"Zerodha {endpoint} request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, dict) or str(payload.get("status", "")).lower() != "success":
            raise ZerodhaAuthenticationError(f"Zerodha {endpoint} authentication was rejected")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ZerodhaAuthenticationError(f"Zerodha {endpoint} returned an invalid payload")
        return data

    def exchange_request_token(
        self,
        *,
        api_key: str,
        api_secret: str,
        request_token: str,
        expected_account_id: str,
    ) -> ZerodhaAuthenticatedSession:
        api_key = str(api_key or "").strip()
        api_secret = str(api_secret or "")
        request_token = str(request_token or "").strip()
        expected_account_id = str(expected_account_id or "").strip()
        if not all((api_key, api_secret, request_token, expected_account_id)):
            raise ValueError("Zerodha API key, API secret, request token and expected account ID are required")

        checksum = hashlib.sha256(
            f"{api_key}{request_token}{api_secret}".encode("utf-8")
        ).hexdigest()
        try:
            response = self.http.post(
                f"{self.base_url}/session/token",
                headers={"X-Kite-Version": "3"},
                data={
                    "api_key": api_key,
                    "request_token": request_token,
                    "checksum": checksum,
                },
                timeout=15,
            )
            token_data = self._data(response, "session/token")
            access_token = str(token_data.get("access_token") or "").strip()
            exchange_user_id = str(token_data.get("user_id") or "").strip()
            if not access_token or not exchange_user_id:
                raise ZerodhaAuthenticationError(
                    "Zerodha session/token response is missing authenticated identity or access token"
                )

            profile_response = self.http.get(
                f"{self.base_url}/user/profile",
                headers={
                    "X-Kite-Version": "3",
                    "Authorization": f"token {api_key}:{access_token}",
                },
                timeout=15,
            )
            profile = self._data(profile_response, "user/profile")
            profile_user_id = str(profile.get("user_id") or "").strip()
            if not profile_user_id or profile_user_id != exchange_user_id:
                raise ZerodhaAuthenticationError(
                    "Zerodha token-exchange identity does not match authenticated profile"
                )
            if profile_user_id != expected_account_id:
                raise ZerodhaAuthenticationError(
                    "Zerodha authenticated account does not match requested account"
                )
            return ZerodhaAuthenticatedSession(
                account_id=profile_user_id,
                access_token=access_token,
            )
        except ZerodhaAuthenticationError:
            raise
        except Exception as exc:
            raise ZerodhaAuthenticationError(
                f"Zerodha authentication exchange failed: {type(exc).__name__}"
            ) from exc

    def invalidate_session(self, *, api_key: str, access_token: str) -> None:
        """Best-effort explicit Kite logout; callers must clear local state regardless."""
        if not api_key.strip() or not access_token.strip():
            return
        try:
            response = self.http.delete(
                f"{self.base_url}/session/token",
                headers={
                    "X-Kite-Version": "3",
                    "Authorization": f"token {api_key.strip()}:{access_token.strip()}",
                },
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:
            raise ZerodhaAuthenticationError(
                f"Zerodha logout request failed: {type(exc).__name__}"
            ) from exc
