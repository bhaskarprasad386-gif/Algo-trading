from __future__ import annotations

import json
from typing import Any
from time import monotonic

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.brokers.connections import broker_connections
from app.brokers.session_store import broker_sessions
from app.brokers.shoonya import ShoonyaAdapter, ShoonyaHistoryUnavailable
from app.brokers.shoonya_auth import ShoonyaAuthenticationError, ShoonyaOAuthAuthenticator
from app.brokers.shoonya_oauth_state import ShoonyaOAuthFlowStore
from app.core.config import settings
from app.core.logger import app_logger

router = APIRouter()

# Reserved process-local namespace distinct from Box Spread real-owner ID (0).
SHOONYA_SESSION_OWNER_ID = 2_147_483_647


def _disconnect_adapter_best_effort(adapter: ShoonyaAdapter) -> None:
    """Keep cleanup failures from masking the original auth/metadata error."""
    try:
        adapter.disconnect()
    except Exception as cleanup_error:
        app_logger.error("Shoonya adapter cleanup failed: %s", type(cleanup_error).__name__)


def _disconnect_client_best_effort(client: ShoonyaRESTClient) -> None:
    try:
        client.disconnect()
    except Exception as cleanup_error:
        app_logger.error("Shoonya client cleanup failed: %s", type(cleanup_error).__name__)



class ShoonyaAuthUrlRequest(BaseModel):
    client_id: str = Field(default="", max_length=200)


class ShoonyaVerifyRequest(BaseModel):
    client_id: str = Field(default="", max_length=200)
    secret_code: str = Field(default="", max_length=500)
    auth_code: str = Field(min_length=1, max_length=2000)
    flow_id: str = Field(min_length=16, max_length=256)
    display_name: str | None = Field(default=None, max_length=100)


class ShoonyaRESTClient:
    """Read-only Shoonya Noren REST client for authenticated owner sessions."""

    base_url = "https://api.shoonya.com/NorenWClientAPI"

    def __init__(self, *, uid: str, account_id: str, access_token: str, expires_at_monotonic: float | None = None, http: Any | None = None):
        self.uid = uid
        self.account_id = account_id
        self.access_token = access_token
        self.expires_at_monotonic = expires_at_monotonic
        self.http = http or requests.Session()

    def _post(self, endpoint: str, payload: dict[str, Any]) -> Any:
        if not self.access_token:
            raise RuntimeError("Shoonya session is disconnected")
        if self.expires_at_monotonic is not None and monotonic() >= self.expires_at_monotonic:
            self.disconnect()
            raise RuntimeError("Shoonya OAuth token expired; reconnect required")
        response = self.http.post(
            f"{self.base_url}/{endpoint}",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "text/plain",
            },
            data="jData=" + json.dumps(payload, separators=(",", ":")),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def get_user_details(self) -> dict[str, Any]:
        return self._post("UserDetails", {"uid": self.uid})

    def get_order_book(self) -> list[dict[str, Any]]:
        result = self._post("OrderBook", {"uid": self.uid})
        if not isinstance(result, list):
            raise RuntimeError("Shoonya OrderBook response is invalid")
        return result

    def get_trade_book(self) -> list[dict[str, Any]]:
        result = self._post("TradeBook", {"uid": self.uid, "actid": self.account_id})
        if not isinstance(result, list):
            raise RuntimeError("Shoonya TradeBook response is invalid")
        return result

    def disconnect(self) -> None:
        token = self.access_token
        token_is_expired = (
            self.expires_at_monotonic is not None
            and monotonic() >= self.expires_at_monotonic
        )
        self.access_token = ""
        self.expires_at_monotonic = None
        # Expired OAuth tokens are invalid for remote logout too. Clear local
        # credentials first and avoid sending a stale bearer token on cleanup.
        if token and not token_is_expired:
            try:
                response = self.http.post(
                    f"{self.base_url}/Logout",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "text/plain",
                    },
                    data="jData=" + json.dumps({"uid": self.uid}, separators=(",", ":")),
                    timeout=10,
                )
                response.raise_for_status()
            except Exception:
                # Local credential clearing must not depend on remote logout.
                pass
        close = getattr(self.http, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Best-effort HTTP transport cleanup must not mask the caller's
                # auth/session failure or leave local token clearing undone.
                pass


shoonya_oauth_flows = ShoonyaOAuthFlowStore()


@router.post("/shoonya/auth-url")
def shoonya_auth_url(
    payload: ShoonyaAuthUrlRequest,
) -> dict:
    client_id = str(settings.SHOONYA_OAUTH_CLIENT_ID or "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="Shoonya OAuth client ID is not configured on the server")
    try:
        flow_id = shoonya_oauth_flows.create(
            owner_id=SHOONYA_SESSION_OWNER_ID,
            client_id=client_id,
        )
        url = ShoonyaOAuthAuthenticator.build_authorization_url(client_id=client_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Shoonya OAuth configuration is invalid") from exc
    return {
        "broker": "shoonya",
        "authorization_url": url,
        "flow_id": flow_id,
        "expires_in": 600,
        "authorization_code_mode": "manual",
        "instructions": "Sign in at Shoonya, copy the auth code from the browser URL (even if the page says Access restricted), then submit it with this flow_id within 10 minutes.",
    }


@router.post("/shoonya/verify")
def shoonya_verify(
    payload: ShoonyaVerifyRequest,
) -> dict:
    client_id = str(settings.SHOONYA_OAUTH_CLIENT_ID or "").strip()
    secret_code = str(settings.SHOONYA_OAUTH_SECRET_CODE or "")
    if not client_id or not secret_code:
        raise HTTPException(status_code=503, detail="Shoonya OAuth client ID and secret code are not configured")
    try:
        pending = shoonya_oauth_flows.consume(
            owner_id=SHOONYA_SESSION_OWNER_ID,
            flow_id=payload.flow_id,
            client_id=client_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Shoonya OAuth flow is invalid, expired, or already used") from exc

    # Reserve ordering before network work: later reconnect attempts supersede
    # earlier ones even if their OAuth exchange completes last.
    owner_id = SHOONYA_SESSION_OWNER_ID
    replacement_generation = broker_sessions.reserve_replacement(owner_id, "shoonya")
    client: ShoonyaRESTClient | None = None
    adapter: ShoonyaAdapter | None = None
    session_installed = False
    previous_connection_item = broker_connections.get(owner_id, "shoonya")
    connection_item: list = []
    try:
        authenticator = ShoonyaOAuthAuthenticator()
        try:
            authenticated = authenticator.exchange_auth_code(
                client_id=client_id,
                secret_code=secret_code,
                auth_code=payload.auth_code,
                expected_uid=pending.uid,
                expected_account_id=pending.account_id,
            )
        finally:
            # The authenticator owns a one-shot requests.Session for the token
            # exchange. Close its pool on both success and failure.
            transport = getattr(authenticator, "http", None)
            close_transport = getattr(transport, "close", None)
            if callable(close_transport):
                try:
                    close_transport()
                except Exception as cleanup_error:
                    app_logger.error(
                        "Shoonya OAuth transport cleanup failed: %s",
                        type(cleanup_error).__name__,
                    )
        client = ShoonyaRESTClient(
            uid=authenticated.uid,
            account_id=authenticated.account_id,
            access_token=authenticated.access_token,
            expires_at_monotonic=authenticated.expires_at_monotonic,
        )
        adapter = ShoonyaAdapter(client)
        adapter.connect(uid=authenticated.uid, account_id=authenticated.account_id)

        installed, previous = broker_sessions.replace_if_reserved(
            owner_id, "shoonya", adapter, replacement_generation
        )
        session_installed = installed
        if not installed:
            raise HTTPException(status_code=409, detail="Shoonya session was superseded by a newer reconnect")
        if previous is not None:
            try:
                previous.disconnect()
            except Exception as logout_error:
                app_logger.error(
                    "Previous shoonya provider logout failed during reconnect: %s",
                    type(logout_error).__name__,
                )
        def commit_connection_metadata() -> None:
            connection_item.append(
                broker_connections.connect(owner_id, "shoonya", payload.display_name)
            )

        metadata_committed = broker_sessions.commit_if_current(
            owner_id,
            "shoonya",
            adapter,
            commit_connection_metadata,
            generation=replacement_generation,
        )
        if not metadata_committed:
            raise HTTPException(status_code=409, detail="Shoonya session was superseded by a newer reconnect")
        item = connection_item[0]
        return {
            "connected": item.connected,
            "broker": "shoonya",
            "uid": authenticated.uid,
            "account_id": authenticated.account_id,
            "display_name": item.display_name,
            "real_trading": False,
            "kill_switch": True,
        }
    except HTTPException:
        if adapter is not None:
            removed = broker_sessions.remove_if_adapter_current(owner_id, "shoonya", adapter)
            if removed:
                metadata_item = connection_item[0] if connection_item else previous_connection_item
                if metadata_item is not None:
                    broker_connections.disconnect_if_current(owner_id, "shoonya", metadata_item)
                _disconnect_adapter_best_effort(adapter)
            elif not session_installed:
                # This adapter never entered the registry (the reservation was
                # superseded before installation), so it is always safe to close.
                _disconnect_adapter_best_effort(adapter)
        elif client is not None:
            _disconnect_client_best_effort(client)
        raise
    except (ShoonyaAuthenticationError, ShoonyaHistoryUnavailable, ValueError) as exc:
        if adapter is not None:
            removed = broker_sessions.remove_if_adapter_current(owner_id, "shoonya", adapter)
            if removed:
                metadata_item = connection_item[0] if connection_item else previous_connection_item
                if metadata_item is not None:
                    broker_connections.disconnect_if_current(owner_id, "shoonya", metadata_item)
                _disconnect_adapter_best_effort(adapter)
            elif not session_installed:
                # This adapter never entered the registry (the reservation was
                # superseded before installation), so it is always safe to close.
                _disconnect_adapter_best_effort(adapter)
        elif client is not None:
            _disconnect_client_best_effort(client)
        raise HTTPException(status_code=401, detail="Shoonya authentication or account verification failed") from exc
    except Exception as exc:
        if adapter is not None:
            removed = broker_sessions.remove_if_adapter_current(owner_id, "shoonya", adapter)
            if removed:
                metadata_item = connection_item[0] if connection_item else previous_connection_item
                if metadata_item is not None:
                    broker_connections.disconnect_if_current(owner_id, "shoonya", metadata_item)
                _disconnect_adapter_best_effort(adapter)
            elif not session_installed:
                # This adapter never entered the registry (the reservation was
                # superseded before installation), so it is always safe to close.
                _disconnect_adapter_best_effort(adapter)
        elif client is not None:
            _disconnect_client_best_effort(client)
        app_logger.error("Shoonya connection setup failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Shoonya connection could not be established") from exc
