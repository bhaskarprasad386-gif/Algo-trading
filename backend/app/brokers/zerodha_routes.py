from __future__ import annotations

from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.connections import broker_connections
from app.brokers.real_owner_auth import require_real_owner_access
from app.brokers.real_owner_state import claim_authenticated_account, set_owner_connection_state
from app.brokers.safety import trading_safety
from app.brokers.session_store import broker_sessions
from app.brokers.zerodha import ZerodhaAdapter, ZerodhaHistoryUnavailable
from app.brokers.zerodha_auth import ZerodhaAuthenticationError, ZerodhaTypeAAuthenticator
from app.core.database import get_db
from app.core.logger import app_logger

router = APIRouter()


class ZerodhaVerifyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)
    api_secret: str = Field(min_length=1, max_length=500)
    request_token: str = Field(min_length=1, max_length=2000)
    expected_account_id: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)


class ZerodhaRESTClient:
    """Read-only Kite Connect REST client with in-memory access-token ownership."""

    base_url = "https://api.kite.trade"

    def __init__(self, *, api_key: str, access_token: str = "", http: Any | None = None):
        self.api_key = str(api_key or "").strip()
        self.access_token = str(access_token or "").strip()
        self.http = http or requests.Session()

    def set_access_token(self, access_token: str) -> None:
        self.access_token = str(access_token or "").strip()

    def _get(self, path: str) -> Any:
        if not self.api_key or not self.access_token:
            raise RuntimeError("Zerodha session is disconnected")
        response = self.http.get(
            f"{self.base_url}/{path}",
            headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {self.api_key}:{self.access_token}",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("status", "")).lower() != "success":
            raise RuntimeError("Zerodha returned an invalid read-only response")
        return payload.get("data")

    def profile(self) -> dict[str, Any]:
        data = self._get("user/profile")
        if not isinstance(data, dict):
            raise RuntimeError("Zerodha profile response is invalid")
        return data

    def orders(self) -> list[dict[str, Any]]:
        data = self._get("orders")
        if not isinstance(data, list):
            raise RuntimeError("Zerodha order book response is invalid")
        return data

    def trades(self) -> list[dict[str, Any]]:
        data = self._get("trades")
        if not isinstance(data, list):
            raise RuntimeError("Zerodha trade book response is invalid")
        return data

    def logout(self) -> None:
        token = self.access_token
        try:
            if token and self.api_key:
                response = self.http.delete(
                    f"{self.base_url}/session/token",
                    headers={
                        "X-Kite-Version": "3",
                        "Authorization": f"token {self.api_key}:{token}",
                    },
                    timeout=10,
                )
                response.raise_for_status()
        except Exception:
            # Always clear local credentials even if remote invalidation is unavailable.
            pass
        finally:
            self.access_token = ""
            close = getattr(self.http, "close", None)
            if callable(close):
                close()


@router.post("/zerodha/verify")
def zerodha_verify(
    payload: ZerodhaVerifyRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Exchange one-time Kite request token, verify identity, and bind one owner account."""
    # Re-authentication must disarm any prior process-local safety state first.
    trading_safety.disable(user_id)
    client: ZerodhaRESTClient | None = None
    adapter: ZerodhaAdapter | None = None
    try:
        authenticated = ZerodhaTypeAAuthenticator().exchange_request_token(
            api_key=payload.api_key,
            api_secret=payload.api_secret,
            request_token=payload.request_token,
            expected_account_id=payload.expected_account_id,
        )
        client = ZerodhaRESTClient(
            api_key=payload.api_key,
            access_token=authenticated.access_token,
        )
        adapter = ZerodhaAdapter(client)
        adapter.connect(
            access_token=authenticated.access_token,
            account_id=authenticated.account_id,
        )
        claim_authenticated_account(db, "zerodha", authenticated.account_id)
        set_owner_connection_state(db, True)

        logout_error = broker_sessions.replace_and_disconnect_previous(user_id, "zerodha", adapter)
        if logout_error is not None:
            app_logger.error("Previous zerodha provider logout failed during reconnect: %s", type(logout_error).__name__)
        item = broker_connections.connect(user_id, "zerodha", payload.display_name)
        trading_safety.disable(user_id)
        return {
            "connected": item.connected,
            "broker": "zerodha",
            "account_id": authenticated.account_id,
            "display_name": item.display_name,
            "real_trading": False,
            "kill_switch": True,
        }
    except HTTPException:
        if adapter is not None:
            adapter.disconnect()
        elif client is not None:
            client.logout()
        db.rollback()
        raise
    except (ZerodhaAuthenticationError, ZerodhaHistoryUnavailable, ValueError) as exc:
        if adapter is not None:
            adapter.disconnect()
        elif client is not None:
            client.logout()
        db.rollback()
        raise HTTPException(status_code=401, detail="Zerodha authentication or account verification failed") from exc
    except Exception as exc:
        if adapter is not None:
            adapter.disconnect()
        elif client is not None:
            client.logout()
        db.rollback()
        app_logger.error("Zerodha durable account binding failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Zerodha account could not be safely bound to the Box Spread owner") from exc
