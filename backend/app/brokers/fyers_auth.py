from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import requests


class FyersAuthenticationError(RuntimeError):
    """Raised when the documented FYERS API v3 login exchange is rejected."""


@dataclass(frozen=True)
class FyersAuthenticatedSession:
    """Broker-verified FYERS identity and short-lived access token."""

    account_id: str
    access_token: str


class FyersTypeAAuthenticator:
    """Exchange a FYERS authorization code and verify the resulting profile.

    The app secret and authorization code are request-scoped. Tokens are
    returned to the caller for in-memory session setup and are never persisted
    by this helper. FYERS access tokens expire daily at 06:30 IST.
    """

    base_url = "https://api-t1.fyers.in"

    def __init__(self, http: Any | None = None) -> None:
        self.http = http or requests.Session()

    @staticmethod
    def _successful_payload(response: Any, endpoint: str) -> dict[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise FyersAuthenticationError(
                f"FYERS {endpoint} request failed: {type(exc).__name__}"
            ) from exc
        if (
            not isinstance(payload, dict)
            or str(payload.get("s", "")).lower() != "ok"
            or payload.get("code") != 200
        ):
            raise FyersAuthenticationError(
                f"FYERS {endpoint} authentication was rejected"
            )
        return payload

    def exchange_auth_code(
        self,
        *,
        app_id: str,
        app_secret: str,
        auth_code: str,
        expected_account_id: str,
    ) -> FyersAuthenticatedSession:
        app_id = str(app_id or "").strip()
        app_secret = str(app_secret or "")
        auth_code = str(auth_code or "").strip()
        expected_account_id = str(expected_account_id or "").strip()
        if not all((app_id, app_secret, auth_code, expected_account_id)):
            raise ValueError(
                "FYERS app ID, app secret, authorization code and expected account ID are required"
            )

        app_id_hash = hashlib.sha256(
            f"{app_id}:{app_secret}".encode("utf-8")
        ).hexdigest()
        try:
            token_response = self.http.post(
                f"{self.base_url}/api/v3/validate-authcode",
                json={
                    "grant_type": "authorization_code",
                    "appIdHash": app_id_hash,
                    "code": auth_code,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            token_payload = self._successful_payload(
                token_response, "validate-authcode"
            )
            access_token = str(token_payload.get("access_token") or "").strip()
            if not access_token:
                raise FyersAuthenticationError(
                    "FYERS token response is missing access_token"
                )

            profile_response = self.http.get(
                f"{self.base_url}/api/v3/profile",
                headers={"Authorization": f"{app_id}:{access_token}"},
                timeout=15,
            )
            profile_payload = self._successful_payload(profile_response, "profile")
            profile = profile_payload.get("data")
            if not isinstance(profile, dict):
                raise FyersAuthenticationError(
                    "FYERS profile response is missing account data"
                )
            authenticated_id = str(profile.get("fy_id") or "").strip()
            if not authenticated_id:
                raise FyersAuthenticationError(
                    "FYERS profile response is missing authenticated account identity"
                )
            if authenticated_id != expected_account_id:
                raise FyersAuthenticationError(
                    "FYERS authenticated account does not match requested account"
                )
            return FyersAuthenticatedSession(
                account_id=authenticated_id,
                access_token=access_token,
            )
        except FyersAuthenticationError:
            raise
        except Exception as exc:
            raise FyersAuthenticationError(
                f"FYERS authentication exchange failed: {type(exc).__name__}"
            ) from exc
