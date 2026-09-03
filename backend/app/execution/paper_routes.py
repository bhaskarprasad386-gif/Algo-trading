"""Authenticated paper-execution API boundary.

Paper state is isolated per authenticated user. Live broker execution stays
behind the existing confirmation, idempotency, and safety gates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.brokers.base import BrokerOrder
from app.core.config import settings
from app.core.security import ALGORITHM
from app.execution.dual_engine import DualExecutionEngine, ExecutionConfig, ExecutionMode, Fill

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


_paper_positions: dict[int, dict] = {}
_paper_orders: dict[int, list[dict]] = {}


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


def _position_from_state(state, symbol: str) -> dict:
    return {
        "symbol": symbol,
        "mode": state.mode.value,
        "quantity": state.quantity,
        "entry_price": state.entry_price,
        "stop_loss": state.stop_loss,
        "target": state.target,
    }


@router.post("/paper/entry")
def paper_entry(request: PaperEntryRequest, user_id: int = Depends(current_user_id)):
    """Simulate one paper long entry for the authenticated user."""
    if user_id in _paper_positions:
        raise HTTPException(status_code=409, detail="A paper position is already active")

    engine = DualExecutionEngine(
        _paper_fill,
        config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct),
    )
    fill = engine.enter(request.price, request.quantity)
    state = engine.paper
    position = _position_from_state(state, symbol="PAPER")
    _paper_positions[user_id] = position
    return {
        "status": "success",
        "mode": state.mode.value,
        "fill": {"price": fill.price, "quantity": fill.quantity},
        "entry_price": state.entry_price,
        "stop_loss": state.stop_loss,
        "target": state.target,
        "position": position,
    }


@router.post("/paper/order")
def paper_order(request: PaperOrderRequest, user_id: int = Depends(current_user_id)):
    """Create a simulated BUY/SELL order without contacting a broker."""
    side = request.transaction_type.strip().upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="transaction_type must be BUY or SELL")

    active = _paper_positions.get(user_id)
    if side == "BUY":
        if active is not None:
            raise HTTPException(status_code=409, detail="A paper position is already active")
        engine = DualExecutionEngine(
            _paper_fill,
            config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct),
        )
        fill = engine.enter(request.price, request.quantity)
        position = _position_from_state(engine.paper, request.symbol)
        _paper_positions[user_id] = position
        order_status = "FILLED"
        pnl = 0.0
    else:
        if active is None or active["symbol"] != request.symbol:
            raise HTTPException(status_code=409, detail="No matching paper position to sell")
        if request.quantity > float(active["quantity"]):
            raise HTTPException(status_code=400, detail="Sell quantity exceeds active paper position")
        fill = Fill(price=request.price, quantity=request.quantity)
        pnl = round((fill.price - float(active["entry_price"])) * fill.quantity, 8)
        if request.quantity == float(active["quantity"]):
            _paper_positions.pop(user_id, None)
        else:
            active["quantity"] = round(float(active["quantity"]) - request.quantity, 8)
        order_status = "FILLED"

    order = {
        "id": f"PAPER-{user_id}-{len(_paper_orders.get(user_id, [])) + 1}",
        "symbol": request.symbol,
        "transaction_type": side,
        "price": fill.price,
        "quantity": fill.quantity,
        "status": order_status,
        "pnl": pnl,
    }
    _paper_orders.setdefault(user_id, []).append(order)
    return {"status": "success", "mode": "paper", "order": order, "position": _paper_positions.get(user_id)}


@router.get("/paper/orders")
def paper_orders(user_id: int = Depends(current_user_id)):
    return {"mode": "paper", "orders": list(_paper_orders.get(user_id, []))}


@router.get("/paper/position")
def paper_position(user_id: int = Depends(current_user_id)):
    position = _paper_positions.get(user_id)
    if position is None:
        return {"status": "flat", "position": None}
    return {"status": "active", "position": position}


@router.post("/paper/exit")
def paper_exit(request: PaperExitRequest, user_id: int = Depends(current_user_id)):
    """Close the active paper position and return realized P&L."""
    position = _paper_positions.get(user_id)
    if position is None:
        return {"status": "flat", "position": None, "pnl": 0.0}

    entry_price = float(position["entry_price"])
    quantity = float(position["quantity"])
    pnl = round((request.price - entry_price) * quantity, 8)
    result = {
        "status": "closed",
        "entry_price": entry_price,
        "exit_price": request.price,
        "quantity": quantity,
        "pnl": pnl,
    }
    _paper_positions.pop(user_id, None)
    return result
