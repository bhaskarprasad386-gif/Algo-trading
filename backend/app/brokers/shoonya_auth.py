from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlencode

import requests


class ShoonyaAuthenticationError(RuntimeError):
    """Raised when the documented Shoonya OAuth exchange is rejected."""


@dataclass(frozen=True)
class ShoonyaAuthenticatedSession:
    uid: str
    account_id: str
    access_token: str
    expires_at_monotonic: float | None = None


class ShoonyaOAuthAuthenticator:
    """Exchange Shoonya's one-time OAuth code for a 24-hour access token.

    The secret code and authorization code are request-scoped. The returned
    access token is intended only for the caller's in-memory broker session.
    """

    authorize_url = "https://api.shoonya.com/OAuthlogin/authorize/oauth"
    api_base_url = "https://api.shoonya.com/NorenWClientAPI"

    def __init__(self, http: Any | None = None) -> None:
        self.http = http or requests.Session()

    @classmethod
    def build_authorization_url(cls, *, client_id: str) -> str:
        client_id = str(client_id or "").strip()
        if not client_id:
            raise ValueError("Shoonya client ID is required")
        return f"{cls.authorize_url}?{urlencode({'client_id': client_id})}"

    @staticmethod
    def _payload(response: Any, endpoint: str) -> dict[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ShoonyaAuthenticationError(
                f"Shoonya {endpoint} request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
            raise ShoonyaAuthenticationError(
                f"Shoonya {endpoint} authentication was rejected"
            )
        return payload

    def _revoke_issued_token(self, access_token: str, uid: str) -> None:
        """Best-effort revoke a token that cannot be safely bound to an account."""
        if not access_token:
            return
        try:
            self.http.post(
                f"{self.api_base_url}/Logout",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "text/plain",
                },
                data="jData=" + json.dumps(
                    {"uid": uid}, separators=(",", ":")
                ),
                timeout=10,
            )
        except Exception:
            # The caller must still reject the untrusted token response.
            pass

    def exchange_auth_code(
        self,
        *,
        client_id: str,
        secret_code: str,
        auth_code: str,
        expected_uid: str,
        expected_account_id: str,
    ) -> ShoonyaAuthenticatedSession:
        client_id = str(client_id or "").strip()
        secret_code = str(secret_code or "")
        auth_code = str(auth_code or "").strip()
        expected_uid = str(expected_uid or "").strip()
        expected_account_id = str(expected_account_id or "").strip()
        if not all((client_id, secret_code, auth_code, expected_uid, expected_account_id)):
            raise ValueError(
                "Shoonya client ID, secret code, auth code, expected UID and account ID are required"
            )

        checksum = hashlib.sha256(
            f"{client_id}{secret_code}{auth_code}".encode("utf-8")
        ).hexdigest()
        try:
            response = self.http.post(
                f"{self.api_base_url}/GenAcsTok",
                headers={"Content-Type": "text/plain"},
                data="jData=" + json.dumps(
                    {"code": auth_code, "checksum": checksum},
                    separators=(",", ":"),
                ),
                timeout=15,
            )
            token_payload = self._payload(response, "GenAcsTok")
            access_token = str(token_payload.get("susertoken") or "").strip()
            authenticated_uid = str(token_payload.get("uid") or "").strip()
            authenticated_account = str(token_payload.get("actid") or "").strip()
            if not access_token:
                raise ShoonyaAuthenticationError(
                    "Shoonya GenAcsTok response is missing access token, UID or account identity"
                )
            if not authenticated_uid or not authenticated_account:
                # A bearer token without complete identity is still issued
                # credential material and must not be left active.
                self._revoke_issued_token(access_token, authenticated_uid or expected_uid)
                raise ShoonyaAuthenticationError(
                    "Shoonya GenAcsTok response is missing access token, UID or account identity"
                )
            if authenticated_uid != expected_uid or authenticated_account != expected_account_id:
                # Never retain a token whose identity failed correlation.
                self._revoke_issued_token(access_token, authenticated_uid)
                if authenticated_uid != expected_uid:
                    raise ShoonyaAuthenticationError(
                        "Shoonya authenticated UID does not match requested UID"
                    )
                raise ShoonyaAuthenticationError(
                    "Shoonya authenticated account does not match requested account"
                )
            return ShoonyaAuthenticatedSession(
                uid=authenticated_uid,
                account_id=authenticated_account,
                access_token=access_token,
                expires_at_monotonic=monotonic() + 24 * 60 * 60,
            )
        except ShoonyaAuthenticationError:
            raise
        except Exception as exc:
            raise ShoonyaAuthenticationError(
                f"Shoonya OAuth exchange failed: {type(exc).__name__}"
            ) from exc
