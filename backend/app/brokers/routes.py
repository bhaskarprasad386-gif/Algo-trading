from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.brokers.connections import broker_connections
from app.brokers.registry import BrokerRegistry

router = APIRouter(prefix="/api/v1/brokers", tags=["brokers"])


class ConnectRequest(BaseModel):
    broker: str = Field(min_length=2, max_length=50)
    display_name: str | None = Field(default=None, max_length=100)


# Temporary authenticated-user dependency boundary. Production auth middleware can replace this
# without changing the route contract. Credentials/tokens are intentionally excluded from requests.
def current_user_id() -> int:
    return 0


@router.get("")
def supported_brokers() -> dict:
    return {"brokers": BrokerRegistry().names()}


@router.get("/connections")
def connections(user_id: int = Depends(current_user_id)) -> dict:
    return {"connections": [c.__dict__ for c in broker_connections.list(user_id)]}


@router.post("/connect")
def connect(payload: ConnectRequest, user_id: int = Depends(current_user_id)) -> dict:
    broker = payload.broker.strip().lower()
    supported = {name.lower() for name in BrokerRegistry().names()}
    if broker not in supported:
        raise HTTPException(status_code=400, detail=f"Unsupported broker: {broker}")
    item = broker_connections.connect(user_id, broker, payload.display_name)
    return {"connected": item.connected, "broker": item.broker, "display_name": item.display_name}


@router.delete("/{broker}")
def disconnect(broker: str, user_id: int = Depends(current_user_id)) -> dict:
    broker_connections.disconnect(user_id, broker.strip().lower())
    return {"connected": False, "broker": broker.strip().lower()}
