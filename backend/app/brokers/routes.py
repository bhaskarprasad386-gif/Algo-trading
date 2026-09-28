from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.angel_one import AngelOneAdapter
from app.brokers.connections import broker_connections
from app.brokers.registry import BrokerRegistry
from app.brokers.safety import trading_safety
from app.core.database import get_db
from app.core.logger import app_logger
from app.execution.paper_routes import current_user_id

router = APIRouter(prefix="/api/v1/brokers", tags=["brokers"])


class RealTradingEnableRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=100)
@router.get("")
def supported_brokers(user_id: int = Depends(current_user_id)) -> dict:
    return {"brokers": BrokerRegistry().names()}


@router.get("/connections")
def connections(user_id: int = Depends(current_user_id)) -> dict:
    return {"connections": [{"broker": c.broker, "connected": c.connected, "display_name": c.display_name, "connected_at": c.connected_at.isoformat() if c.connected_at else None} for c in broker_connections.list(user_id)]}


@router.get("/safety")
def safety_status(user_id: int = Depends(current_user_id)) -> dict:
    state = trading_safety.get(user_id)
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "enabled_at": state.enabled_at.isoformat() if state.enabled_at else None}


@router.post("/safety/enable")
def enable_real_trading(payload: RealTradingEnableRequest, user_id: int = Depends(current_user_id)) -> dict:
    if payload.confirmation.strip().upper() != "ENABLE REAL TRADING":
        raise HTTPException(status_code=400, detail="Explicit confirmation required: ENABLE REAL TRADING")
    connection = next((c for c in broker_connections.list(user_id) if c.connected), None)
    if connection is None:
        raise HTTPException(status_code=409, detail="Connect a broker before enabling real trading")
    state = trading_safety.enable(user_id)
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "live_order_routing": False, "message": "Real trading armed, but live order routing is still disabled."}


@router.post("/safety/disable")
def disable_real_trading(user_id: int = Depends(current_user_id)) -> dict:
    state = trading_safety.disable(user_id)
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "message": "Real trading disabled and kill switch engaged."}


@router.post("/safety/kill-switch")
def emergency_kill_switch(user_id: int = Depends(current_user_id)) -> dict:
    state = trading_safety.disable(user_id)
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "message": "Emergency kill switch engaged."}


@router.post("/connect")
def connect(payload: ConnectRequest, user_id: int = Depends(current_user_id)) -> dict:
    broker = payload.broker.strip().lower()
    if broker != "angel_one":
        raise HTTPException(status_code=400, detail=f"Unsupported broker: {broker}")
    adapter = AngelOneAdapter()
    try:
        result = adapter.connect(api_key=payload.api_key, client_code=payload.client_code, password=payload.password, totp_secret=payload.totp_secret)
    except Exception as exc:
        app_logger.error("Angel One connection failed: %s", exc)
        raise HTTPException(status_code=502, detail="Angel One connection failed") from exc
    old = _sessions.get((user_id, broker))
    if old is not None:
        old.disconnect()
    _sessions[(user_id, broker)] = adapter
    item = broker_connections.connect(user_id, broker, payload.display_name)
    trading_safety.disable(user_id)
    return {"connected": item.connected, "broker": item.broker, "display_name": item.display_name, "client_code": result.get("client_code"), "real_trading": False}


@router.get("/{broker}/status")
def status(broker: str, user_id: int = Depends(current_user_id)) -> dict:
    key = (user_id, broker.strip().lower())
    adapter = _sessions.get(key)
    item = broker_connections.get(user_id, key[1])
    state = trading_safety.get(user_id)
    return {"broker": key[1], "connected": bool(adapter and adapter.connected and item and item.connected), "display_name": item.display_name if item else None, "real_trading": state.real_trading_enabled, "kill_switch": state.kill_switch}


@router.delete("/{broker}")
def disconnect(broker: str, user_id: int = Depends(current_user_id)) -> dict:
    name = broker.strip().lower()
    adapter = _sessions.pop((user_id, name), None)
    if adapter is not None:
        adapter.disconnect()
    broker_connections.disconnect(user_id, name)
    trading_safety.disable(user_id)
    return {"connected": False, "broker": name, "real_trading": False, "kill_switch": True}


@router.get("/web/settings", include_in_schema=False)
def broker_settings_page() -> HTMLResponse:
    page = Path(__file__).resolve().parents[3] / "web" / "dashboard" / "broker.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Broker settings page unavailable")
    return HTMLResponse(content=page.read_text(encoding="utf-8"), media_type="text/html")
