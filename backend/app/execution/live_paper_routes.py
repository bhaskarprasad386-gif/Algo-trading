"""Authenticated broker-independent live-paper execution API.

These endpoints consume market-data ticks but never submit broker orders.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.execution.live_paper import LivePaperExecution
from app.execution.paper_routes import current_user_id
from app.models import Order

router = APIRouter(prefix="/api/v1/execution/live-paper", tags=["Live Paper"])
engine = LivePaperExecution()


class LivePaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    side: str = Field(min_length=3, max_length=4)
    quantity: int = Field(gt=0)
    order_type: str = Field(default="MARKET", min_length=5, max_length=8)
    price: float | None = Field(default=None, gt=0)
    trigger_price: float | None = Field(default=None, gt=0)
    client_order_id: str | None = Field(default=None, min_length=1, max_length=128)


class LivePaperTickRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    ltp: float = Field(gt=0)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)


def _payload(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.transaction_type,
        "order_type": order.order_type,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "price": order.price,
        "average_fill_price": order.average_fill_price,
        "trigger_price": order.trigger_price,
        "status": order.status,
        "time_in_force": order.time_in_force,
    }


@router.post("/orders")
def place_order(request: LivePaperOrderRequest, user_id: int = Depends(current_user_id),
                db: Session = Depends(get_db)):
    try:
        order = engine.place(db, user_id=user_id, symbol=request.symbol, side=request.side,
                             quantity=request.quantity, order_type=request.order_type,
                             price=request.price, trigger_price=request.trigger_price,
                             client_order_id=request.client_order_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "success", "mode": "live-paper", "broker_order_sent": False, "order": _payload(order)}


@router.post("/ticks")
def process_tick(request: LivePaperTickRequest, user_id: int = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    try:
        fills = engine.on_tick(db, user_id=user_id, tick=request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "success", "mode": "live-paper", "broker_order_sent": False,
            "symbol": request.symbol.upper(), "fills": fills}


@router.get("/orders")
def list_orders(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = (db.query(Order).filter(Order.user_id == user_id).order_by(Order.id.desc()).limit(200).all())
    return {"status": "success", "mode": "live-paper", "broker_order_sent": False,
            "orders": [_payload(row) for row in rows]}


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, user_id: int = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    try:
        order = engine.cancel(db, user_id=user_id, order_id=order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "success", "mode": "live-paper", "broker_order_sent": False, "order": _payload(order)}


@router.get("/mtm/{symbol}")
def mtm(symbol: str, price: float, user_id: int = Depends(current_user_id),
        db: Session = Depends(get_db)):
    try:
        result = engine.mark_to_market(db, user_id=user_id, symbol=symbol, price=price)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "success", "mode": "live-paper", "broker_order_sent": False, **result}
