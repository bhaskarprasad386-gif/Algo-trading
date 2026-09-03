"""Authenticated, persistent paper-execution API boundary.

Paper execution is isolated per authenticated user and persists positions,
orders, virtual balance, and realized P&L in the application database.
Live broker execution remains disabled behind the broker safety layer.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import ALGORITHM
from app.execution.dual_engine import DualExecutionEngine, ExecutionConfig, ExecutionMode, Fill
from app.models import Order, Position, TradingAccount

router = APIRouter(prefix="/api/v1/execution", tags=["Execution"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class PaperEntryRequest(BaseModel):
    price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    stop_loss_pct: float = Field(0.02, ge=0)
    target_pct: float = Field(0.04, ge=0)


class PaperExitRequest(BaseModel):
    price: float = Field(..., gt=0)


class PaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    transaction_type: str = Field(min_length=3, max_length=4)
    price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    stop_loss_pct: float = Field(0.02, ge=0)
    target_pct: float = Field(0.04, ge=0)


def current_user_id(token: str = Depends(oauth2_scheme)) -> int:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", "0"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="Invalid authenticated user")
    return user_id


def _paper_fill(mode: ExecutionMode, price: float, quantity: float) -> Fill:
    if mode is not ExecutionMode.PAPER:
        raise RuntimeError("paper endpoint cannot execute live orders")
    return Fill(price=price, quantity=quantity)


def _account(db: Session, user_id: int) -> TradingAccount:
    account = db.query(TradingAccount).filter(TradingAccount.user_id == user_id).first()
    if account is None or not account.is_active:
        raise HTTPException(status_code=404, detail="Paper trading account not found")
    if account.mode.upper() != "PAPER":
        raise HTTPException(status_code=409, detail="Trading account is not in paper mode")
    return account


def _position(db: Session, user_id: int) -> Position | None:
    return (
        db.query(Position)
        .filter(Position.user_id == user_id, Position.quantity > 0)
        .order_by(Position.id.desc())
        .first()
    )


def _buy_cost(price: float, quantity: float) -> float:
    return round(price * quantity, 8)


def _position_payload(position: Position | None) -> dict | None:
    if position is None:
        return None
    return {
        "symbol": position.symbol,
        "mode": "paper",
        "quantity": float(position.quantity),
        "entry_price": float(position.average_price),
        "stop_loss": position.stop_loss,
        "target": position.target,
    }


def _create_order(
    db: Session,
    *,
    user_id: int,
    symbol: str,
    side: str,
    price: float,
    quantity: float,
    pnl: float = 0.0,
) -> dict:
    order_id = f"PAPER-{user_id}-{uuid.uuid4().hex[:16]}"
    order = Order(
        order_id=order_id,
        symbol=symbol,
        quantity=int(quantity) if float(quantity).is_integer() else round(quantity),
        transaction_type=side,
        status="FILLED",
        user_id=user_id,
        price=price,
        pnl=pnl,
    )
    db.add(order)
    db.flush()
    return {
        "id": order.order_id,
        "symbol": order.symbol,
        "transaction_type": order.transaction_type,
        "price": price,
        "quantity": quantity,
        "status": order.status,
        "pnl": pnl,
    }


@router.post("/paper/entry")
def paper_entry(
    request: PaperEntryRequest,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Simulate one paper long entry and reserve its cash from the account."""
    if _position(db, user_id) is not None:
        raise HTTPException(status_code=409, detail="A paper position is already active")
    account = _account(db, user_id)
    cost = _buy_cost(request.price, request.quantity)
    if account.virtual_balance < cost:
        raise HTTPException(status_code=400, detail="Insufficient paper balance")

    engine = DualExecutionEngine(
        _paper_fill,
        config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct),
    )
    fill = engine.enter(request.price, request.quantity)
    state = engine.paper
    position = Position(
        user_id=user_id,
        symbol="PAPER",
        quantity=int(request.quantity) if request.quantity.is_integer() else round(request.quantity),
        average_price=state.entry_price,
        stop_loss=state.stop_loss,
        target=state.target,
    )
    account.virtual_balance = round(account.virtual_balance - cost, 8)
    db.add(position)
    order = _create_order(
        db, user_id=user_id, symbol="PAPER", side="BUY", price=fill.price, quantity=fill.quantity
    )
    db.commit()
    return {
        "status": "success",
        "mode": state.mode.value,
        "fill": {"price": fill.price, "quantity": fill.quantity},
        "entry_price": state.entry_price,
        "stop_loss": state.stop_loss,
        "target": state.target,
        "position": _position_payload(position),
        "order": order,
        "virtual_balance": account.virtual_balance,
        "realized_pnl": account.realized_pnl,
    }


@router.post("/paper/order")
def paper_order(
    request: PaperOrderRequest,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Create a simulated BUY/SELL order with persistent paper state."""
    side = request.transaction_type.strip().upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="transaction_type must be BUY or SELL")

    account = _account(db, user_id)
    active = _position(db, user_id)
    if side == "BUY":
        if active is not None:
            raise HTTPException(status_code=409, detail="A paper position is already active")
        cost = _buy_cost(request.price, request.quantity)
        if account.virtual_balance < cost:
            raise HTTPException(status_code=400, detail="Insufficient paper balance")
        engine = DualExecutionEngine(
            _paper_fill,
            config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct),
        )
        fill = engine.enter(request.price, request.quantity)
        state = engine.paper
        active = Position(
            user_id=user_id,
            symbol=request.symbol,
            quantity=int(request.quantity) if request.quantity.is_integer() else round(request.quantity),
            average_price=state.entry_price,
            stop_loss=state.stop_loss,
            target=state.target,
        )
        db.add(active)
        account.virtual_balance = round(account.virtual_balance - cost, 8)
        pnl = 0.0
        order = _create_order(
            db, user_id=user_id, symbol=request.symbol, side=side, price=fill.price, quantity=fill.quantity
        )
    else:
        if active is None or active.symbol != request.symbol:
            raise HTTPException(status_code=409, detail="No matching paper position to sell")
        if request.quantity > float(active.quantity):
            raise HTTPException(status_code=400, detail="Sell quantity exceeds active paper position")
        fill = Fill(price=request.price, quantity=request.quantity)
        pnl = round((fill.price - float(active.average_price)) * fill.quantity, 8)
        account.virtual_balance = round(account.virtual_balance + _buy_cost(fill.price, fill.quantity), 8)
        account.realized_pnl = round(account.realized_pnl + pnl, 8)
        active.quantity = int(float(active.quantity) - request.quantity)
        if active.quantity <= 0:
            db.delete(active)
        order = _create_order(
            db, user_id=user_id, symbol=request.symbol, side=side, price=fill.price, quantity=fill.quantity, pnl=pnl
        )

    db.commit()
    remaining = None if side == "SELL" and active.quantity <= 0 else active
    return {
        "status": "success",
        "mode": "paper",
        "order": order,
        "position": _position_payload(remaining),
        "virtual_balance": account.virtual_balance,
        "realized_pnl": account.realized_pnl,
    }


@router.get("/paper/orders")
def paper_orders(
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    orders = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.order_id.like(f"PAPER-{user_id}-%"))
        .order_by(Order.id.asc())
        .all()
    )
    return {
        "mode": "paper",
        "orders": [
            {
                "id": item.order_id,
                "symbol": item.symbol,
                "transaction_type": item.transaction_type,
                "price": item.price,
                "quantity": float(item.quantity),
                "status": item.status,
                "pnl": float(item.pnl or 0.0),
            }
            for item in orders
        ],
    }


@router.get("/paper/position")
def paper_position(
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    position = _position(db, user_id)
    if position is None:
        return {"status": "flat", "position": None}
    return {"status": "active", "position": _position_payload(position)}


@router.post("/paper/exit")
def paper_exit(
    request: PaperExitRequest,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Close the active paper position, return proceeds, and persist realized P&L."""
    account = _account(db, user_id)
    position = _position(db, user_id)
    if position is None:
        return {
            "status": "flat",
            "position": None,
            "pnl": 0.0,
            "virtual_balance": account.virtual_balance,
            "realized_pnl": account.realized_pnl,
        }

    entry_price = float(position.average_price)
    quantity = float(position.quantity)
    pnl = round((request.price - entry_price) * quantity, 8)
    proceeds = _buy_cost(request.price, quantity)
    account.virtual_balance = round(account.virtual_balance + proceeds, 8)
    account.realized_pnl = round(account.realized_pnl + pnl, 8)
    order = _create_order(
        db, user_id=user_id, symbol=position.symbol, side="SELL", price=request.price, quantity=quantity, pnl=pnl
    )
    db.delete(position)
    db.commit()
    return {
        "status": "closed",
        "entry_price": entry_price,
        "exit_price": request.price,
        "quantity": quantity,
        "pnl": pnl,
        "order": order,
        "virtual_balance": account.virtual_balance,
        "realized_pnl": account.realized_pnl,
    }
