from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.brokers.angel_one import AngelOneAdapter
from app.brokers.connections import broker_connections
from app.brokers.registry import BrokerRegistry
from app.core.config import settings
from app.core.security import ALGORITHM

router = APIRouter(prefix="/api/v1/brokers", tags=["brokers"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class ConnectRequest(BaseModel):
    broker: str = Field(min_length=2, max_length=50)
    display_name: str | None = Field(default=None, max_length=100)
    api_key: str = Field(min_length=1, max_length=300)
    client_code: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=300)
    totp_secret: str = Field(min_length=1, max_length=300)


_sessions: dict[tuple[int, str], AngelOneAdapter] = {}


def current_user_id(token: str = Depends(oauth2_scheme)) -> int:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", "0"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="Invalid authenticated user")
    return user_id


@router.get("",)
def supported_brokers(user_id: int = Depends(current_user_id)) -> dict:
    return {"brokers": BrokerRegistry().names()}


@router.get("/connections")
def connections(user_id: int = Depends(current_user_id)) -> dict:
    return {"connections": [{"broker": c.broker, "connected": c.connected, "display_name": c.display_name, "connected_at": c.connected_at.isoformat() if c.connected_at else None} for c in broker_connections.list(user_id)]}


@router.post("/connect")
def connect(payload: ConnectRequest, user_id: int = Depends(current_user_id)) -> dict:
    broker = payload.broker.strip().lower()
    if broker != "angel_one":
        raise HTTPException(status_code=400, detail=f"Unsupported broker: {broker}")
    adapter = AngelOneAdapter()
    try:
        result = adapter.connect(api_key=payload.api_key, client_code=payload.client_code, password=payload.password, totp_secret=payload.totp_secret)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Angel One connection failed: {exc}") from exc
    old = _sessions.get((user_id, broker))
    if old is not None:
        old.disconnect()
    _sessions[(user_id, broker)] = adapter
    item = broker_connections.connect(user_id, broker, payload.display_name)
    return {"connected": item.connected, "broker": item.broker, "display_name": item.display_name, "client_code": result.get("client_code"), "real_trading": False}


@router.get("/{broker}/status")
def status(broker: str, user_id: int = Depends(current_user_id)) -> dict:
    key = (user_id, broker.strip().lower())
    adapter = _sessions.get(key)
    item = broker_connections.get(user_id, key[1])
    return {"broker": key[1], "connected": bool(adapter and adapter.connected and item and item.connected), "display_name": item.display_name if item else None, "real_trading": False}


@router.delete("/{broker}")
def disconnect(broker: str, user_id: int = Depends(current_user_id)) -> dict:
    name = broker.strip().lower()
    adapter = _sessions.pop((user_id, name), None)
    if adapter is not None:
        adapter.disconnect()
    broker_connections.disconnect(user_id, name)
    return {"connected": False, "broker": name, "real_trading": False}


@router.get("/web/settings", include_in_schema=False)
def broker_settings_page() -> HTMLResponse:
    page = Path(__file__).resolve().parents[3] / "web" / "dashboard" / "broker.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Broker settings page unavailable")
    return HTMLResponse(content=page.read_text(encoding="utf-8"), media_type="text/html")
