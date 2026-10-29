from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.angel_one import AngelOneAdapter
from app.brokers.connections import broker_connections
from app.brokers.registry import BrokerRegistry
from app.brokers.safety import trading_safety
from app.brokers.real_owner_auth import RealOwnerLogin, login_real_owner, require_real_owner_access
from app.brokers.real_owner_state import claim_authenticated_account, disable_owner_trading, set_owner_connection_state
from app.core.database import get_db
from app.core.logger import app_logger

router = APIRouter(prefix="/api/v1/brokers", tags=["brokers"])


class ConnectRequest(BaseModel):
    broker: str = Field(min_length=2, max_length=50)
    display_name: str | None = Field(default=None, max_length=100)
    api_key: str = Field(min_length=1, max_length=300)
    client_code: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=300)
    totp_secret: str = Field(min_length=1, max_length=300)


class RealTradingEnableRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=100)


_sessions: dict[tuple[int, str], AngelOneAdapter] = {}


@router.post("/owner-auth/login")
def owner_login(payload: RealOwnerLogin, request: Request):
    """Issue a short-lived token for the separately configured Box Spread owner."""
    return login_real_owner(payload, request)


@router.get("")
def supported_brokers(user_id: int = Depends(require_real_owner_access)) -> dict:
    return {"brokers": BrokerRegistry().names()}


@router.get("/connections")
def connections(user_id: int = Depends(require_real_owner_access)) -> dict:
    return {"connections": [{"broker": c.broker, "connected": c.connected, "display_name": c.display_name, "connected_at": c.connected_at.isoformat() if c.connected_at else None} for c in broker_connections.list(user_id)]}


@router.get("/safety")
def safety_status(user_id: int = Depends(require_real_owner_access)) -> dict:
    state = trading_safety.get(user_id)
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "enabled_at": state.enabled_at.isoformat() if state.enabled_at else None}


@router.post("/safety/enable")
def enable_real_trading(payload: RealTradingEnableRequest, user_id: int = Depends(require_real_owner_access)) -> dict:
    if payload.confirmation.strip().upper() != "ENABLE REAL TRADING":
        raise HTTPException(status_code=400, detail="Explicit confirmation required: ENABLE REAL TRADING")

    # Fail closed until durable single-account ownership, session lifecycle,
    # order reconciliation, risk controls, and recovery are implemented.
    raise HTTPException(
        status_code=503,
        detail="Real-trading arming is unavailable until durable account ownership and execution safety controls are implemented",
    )


@router.post("/safety/disable")
def disable_real_trading(db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    # Always disarm process-local state first, even if durable storage is unavailable.
    trading_safety.disable(user_id)
    try:
        state = disable_owner_trading(db)
    except Exception as exc:
        db.rollback()
        app_logger.error("Durable Box Spread disable failed: %s", exc)
        raise HTTPException(status_code=503, detail="Trading is disabled locally but durable kill-switch state could not be confirmed") from exc
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "message": "Real trading disabled and kill switch persisted."}


@router.post("/safety/kill-switch")
def emergency_kill_switch(db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    # Engage the local kill switch before attempting the durable write.
    trading_safety.disable(user_id)
    try:
        state = disable_owner_trading(db)
    except Exception as exc:
        db.rollback()
        app_logger.error("Durable Box Spread kill-switch update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Kill switch is engaged locally but durable state could not be confirmed") from exc
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "message": "Emergency kill switch engaged and persisted."}


@router.post("/connect")
def connect(payload: ConnectRequest, db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    broker = payload.broker.strip().lower()
    if broker != "angel_one":
        raise HTTPException(status_code=400, detail=f"Unsupported broker: {broker}")
    adapter = AngelOneAdapter()
    try:
        result = adapter.connect(api_key=payload.api_key, client_code=payload.client_code, password=payload.password, totp_secret=payload.totp_secret)
    except Exception as exc:
        app_logger.error("Angel One connection failed: %s", exc)
        raise HTTPException(status_code=502, detail="Angel One connection failed") from exc
    try:
        claim_authenticated_account(db, broker, result.get("client_code") or "")
    except HTTPException:
        adapter.disconnect()
        raise
    except Exception as exc:
        adapter.disconnect()
        db.rollback()
        app_logger.error("Durable Box Spread account binding failed: %s", exc)
        raise HTTPException(status_code=503, detail="Durable Box Spread account ownership is unavailable") from exc
    try:
        set_owner_connection_state(db, True)
    except Exception as exc:
        adapter.disconnect()
        db.rollback()
        app_logger.error("Durable Box Spread connection state update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Durable Box Spread connection state is unavailable") from exc
    old = _sessions.get((user_id, broker))
    if old is not None:
        old.disconnect()
    _sessions[(user_id, broker)] = adapter
    item = broker_connections.connect(user_id, broker, payload.display_name)
    trading_safety.disable(user_id)
    return {"connected": item.connected, "broker": item.broker, "display_name": item.display_name, "client_code": result.get("client_code"), "real_trading": False}


@router.get("/{broker}/status")
def status(broker: str, user_id: int = Depends(require_real_owner_access)) -> dict:
    key = (user_id, broker.strip().lower())
    adapter = _sessions.get(key)
    item = broker_connections.get(user_id, key[1])
    state = trading_safety.get(user_id)
    return {"broker": key[1], "connected": bool(adapter and adapter.connected and item and item.connected), "display_name": item.display_name if item else None, "real_trading": state.real_trading_enabled, "kill_switch": state.kill_switch}


@router.delete("/{broker}")
def disconnect(broker: str, db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    name = broker.strip().lower()
    adapter = _sessions.pop((user_id, name), None)
    if adapter is not None:
        adapter.disconnect()
    broker_connections.disconnect(user_id, name)
    trading_safety.disable(user_id)
    try:
        set_owner_connection_state(db, False)
    except Exception as exc:
        db.rollback()
        app_logger.error("Durable Box Spread disconnect state update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Broker disconnected locally but durable connection state could not be confirmed") from exc
    return {"connected": False, "broker": name, "real_trading": False, "kill_switch": True}


@router.get("/web/settings", include_in_schema=False)
def broker_settings_page() -> HTMLResponse:
    page = Path(__file__).resolve().parents[3] / "web" / "dashboard" / "broker.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Broker settings page unavailable")
    return HTMLResponse(content=page.read_text(encoding="utf-8"), media_type="text/html")
