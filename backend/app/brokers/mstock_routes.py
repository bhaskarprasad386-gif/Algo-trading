from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.connections import broker_connections
from app.brokers.mstock import MStockAdapter, MStockAuthenticationError
from app.brokers.real_owner_auth import require_real_owner_access
from app.brokers.real_owner_state import claim_authenticated_account, set_owner_connection_state
from app.brokers.safety import trading_safety
from app.brokers.session_store import broker_sessions
from app.core.database import get_db
from app.core.logger import app_logger

router = APIRouter()


class MStockLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=300)


class MStockVerifyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=300)
    account_id: str = Field(min_length=1, max_length=100)
    auth_method: Literal["OTP", "TOTP"]
    otp: str | None = Field(default=None, min_length=1, max_length=32)
    totp: str | None = Field(default=None, min_length=1, max_length=32)
    display_name: str | None = Field(default=None, max_length=100)


@router.post("/mstock/login")
def mstock_begin_login(
    payload: MStockLoginRequest,
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Start m.Stock Type A login; credentials are used only for this request."""
    adapter = MStockAdapter()
    try:
        result = adapter.begin_login(username=payload.username, password=payload.password)
        return result
    except (MStockAuthenticationError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="m.Stock login could not be started") from exc
    finally:
        adapter.disconnect()


@router.post("/mstock/verify")
def mstock_verify_login(
    with broker_sessions.transition(user_id):
        payload: MStockVerifyRequest,
        db: Session = Depends(get_db),
        user_id: int = Depends(require_real_owner_access),
    ) -> dict:
        """Exchange OTP/TOTP, verify broker-returned identity, then bind one owner account."""
        if payload.auth_method == "OTP" and (not payload.otp or payload.totp):
            raise HTTPException(status_code=422, detail="Provide otp only when auth_method is OTP")
        if payload.auth_method == "TOTP" and (not payload.totp or payload.otp):
            raise HTTPException(status_code=422, detail="Provide totp only when auth_method is TOTP")

        # Re-authentication must disarm any prior process-local safety state first.
        trading_safety.disable(user_id)
        adapter = MStockAdapter()
        try:
            if payload.auth_method == "OTP":
                result = adapter.complete_otp(
                    api_key=payload.api_key,
                    otp=payload.otp or "",
                    account_id=payload.account_id,
                )
            else:
                result = adapter.complete_totp(
                    api_key=payload.api_key,
                    totp=payload.totp or "",
                    account_id=payload.account_id,
                )

            authenticated_account = str(result["account_id"])
            # Bind only the account ID returned by m.Stock after token exchange;
            # never persist the user-supplied expected ID as proof of identity.
            claim_authenticated_account(db, "mstock", authenticated_account)
            try:
                set_owner_connection_state(db, True)
            except Exception:
                db.rollback()
                raise

            logout_error = broker_sessions.replace_and_disconnect_previous(user_id, "mstock", adapter)
            if logout_error is not None:
                app_logger.error("Previous mstock provider logout failed during reconnect: %s", type(logout_error).__name__)
            item = broker_connections.connect(user_id, "mstock", payload.display_name)
            trading_safety.disable(user_id)
            return {
                "connected": item.connected,
                "broker": "mstock",
                "account_id": authenticated_account,
                "display_name": item.display_name,
                "real_trading": False,
                "kill_switch": True,
            }
        except HTTPException:
            adapter.disconnect()
            raise
        except (MStockAuthenticationError, ValueError) as exc:
            adapter.disconnect()
            raise HTTPException(status_code=401, detail="m.Stock authentication or account verification failed") from exc
        except Exception as exc:
            adapter.disconnect()
            db.rollback()
            app_logger.error("m.Stock durable account binding failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="m.Stock account could not be safely bound to the Box Spread owner") from exc
