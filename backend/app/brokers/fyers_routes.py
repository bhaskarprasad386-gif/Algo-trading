from __future__ import annotations

from typing import Any
import os

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.connections import broker_connections
from app.brokers.fyers import FyersAdapter, FyersHistoryUnavailable
from app.brokers.fyers_auth import FyersAuthenticationError, FyersTypeAAuthenticator
from app.brokers.fyers_oauth_state import FyersOAuthStateStore
from app.brokers.real_owner_auth import require_real_owner_access
from app.brokers.real_owner_state import claim_authenticated_account, set_owner_connection_state
from app.brokers.safety import trading_safety
from app.brokers.session_store import broker_sessions
from app.core.database import get_db
from app.core.logger import app_logger

router = APIRouter()


class FyersAuthUrlRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)


class FyersVerifyRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    app_secret: str = Field(min_length=1, max_length=500)
    auth_code: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=16, max_length=256)
    expected_account_id: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)


class FyersRESTClient:
    """Minimal read-only FYERS v3 REST client; never exposes order mutation."""

    base_url = "https://api-t1.fyers.in/api/v3"

    def __init__(self, *, app_id: str, access_token: str, http: Any | None = None):
        self.app_id = app_id
        self.access_token = access_token
        self.http = http or requests.Session()

    def _get(self, path: str) -> dict[str, Any]:
        if not self.access_token:
            raise RuntimeError("FYERS session is disconnected")
        response = self.http.get(
            f"{self.base_url}/{path}",
            headers={"Authorization": f"{self.app_id}:{self.access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("FYERS returned an invalid response")
        return payload

    def get_profile(self) -> dict[str, Any]:
        return self._get("profile")

    def orderbook(self) -> dict[str, Any]:
        payload = self._get("orders")
        return {"s": payload.get("s"), "code": payload.get("code"), "orderBook": payload.get("orderBook")}

    def tradebook(self) -> dict[str, Any]:
        payload = self._get("tradebook")
        return {"s": payload.get("s"), "code": payload.get("code"), "tradeBook": payload.get("tradeBook")}

    def disconnect(self) -> None:
        token = self.access_token
        self.access_token = ""
        if not token:
            return
        try:
            self.http.post(
                f"{self.base_url}/logout",
                headers={"Authorization": f"{self.app_id}:{token}"},
                timeout=10,
            )
        except Exception:
            # Local credential clearing is unconditional; remote logout is best effort.
            pass
        close = getattr(self.http, "close", None)
        if callable(close):
            close()


fyers_oauth_states = FyersOAuthStateStore()


def _configured_redirect_uri() -> str:
    # The callback is deployment configuration, not caller-controlled input.
    # This prevents an authenticated UI caller from turning the broker login
    # flow into an arbitrary redirect endpoint.
    return str(os.getenv("FYERS_REDIRECT_URI") or "").strip()


@router.post("/fyers/auth-url")
def fyers_auth_url(
    payload: FyersAuthUrlRequest,
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Create a short-lived, single-use state and return the official login URL."""
    redirect_uri = _configured_redirect_uri()
    if not redirect_uri:
        raise HTTPException(status_code=503, detail="FYERS redirect URI is not configured on the server")
    try:
        state, url = fyers_oauth_states.create(
            app_id=payload.app_id,
            redirect_uri=redirect_uri,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Configured FYERS redirect URI is invalid") from exc
    return {"broker": "fyers", "authorization_url": url, "state": state, "expires_in": 600}


@router.post("/fyers/verify")
def fyers_verify(
    payload: FyersVerifyRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Consume OAuth state, exchange the auth code, verify fy_id and bind one owner account."""
    try:
        fyers_oauth_states.consume(
            state=payload.state,
            app_id=payload.app_id,
            redirect_uri=_configured_redirect_uri(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="FYERS OAuth state is invalid, expired, or already used") from exc

    authenticator = FyersTypeAAuthenticator()
    client: FyersRESTClient | None = None
    adapter: FyersAdapter | None = None
    try:
        authenticated = authenticator.exchange_auth_code(
            app_id=payload.app_id,
            app_secret=payload.app_secret,
            auth_code=payload.auth_code,
            expected_account_id=payload.expected_account_id,
        )
        client = FyersRESTClient(
            app_id=payload.app_id,
            access_token=authenticated.access_token,
        )
        adapter = FyersAdapter(client)
        # Re-read the authenticated profile using the session client before binding.
        adapter.connect(account_id=authenticated.account_id)
        claim_authenticated_account(db, "fyers", authenticated.account_id)
        set_owner_connection_state(db, True)

        previous = broker_sessions.replace(user_id, "fyers", adapter)
        if previous is not None:
            previous.disconnect()
        item = broker_connections.connect(user_id, "fyers", payload.display_name)
        trading_safety.disable(user_id)
        return {
            "connected": item.connected,
            "broker": "fyers",
            "account_id": authenticated.account_id,
            "display_name": item.display_name,
            "real_trading": False,
            "kill_switch": True,
        }
    except HTTPException:
        if adapter is not None:
            adapter.disconnect()
        elif client is not None:
            client.disconnect()
        db.rollback()
        raise
    except (FyersAuthenticationError, FyersHistoryUnavailable, ValueError) as exc:
        if adapter is not None:
            adapter.disconnect()
        elif client is not None:
            client.disconnect()
        db.rollback()
        raise HTTPException(status_code=401, detail="FYERS authentication or account verification failed") from exc
    except Exception as exc:
        if adapter is not None:
            adapter.disconnect()
        elif client is not None:
            client.disconnect()
        db.rollback()
        app_logger.error("FYERS durable account binding failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="FYERS account could not be safely bound to the Box Spread owner") from exc
