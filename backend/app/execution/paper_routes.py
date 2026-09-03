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
from app.execution.payoff import PayoffLeg, payoff_summary
from app.execution.strategy_legs import StrategyLegInput, build_cash_future_strategy, build_strategy_legs
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


class ScannerPaperEntryRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    cash_price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    future_price: float | None = Field(default=None, gt=0)
    gap: float | None = None
    net_profit: float | None = None
    executable: bool = True
    stop_loss_pct: float = Field(0.02, ge=0)
    target_pct: float = Field(0.04, ge=0)


class PaperPayoffLegRequest(BaseModel):
    kind: str = Field(min_length=4, max_length=8)
    side: str = Field(min_length=3, max_length=4)
    strike: float | None = Field(default=None, gt=0)
    entry_price: float = Field(..., ge=0)
    quantity: float = Field(..., gt=0)
    multiplier: float = Field(1.0, gt=0)


class PaperPayoffRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    underlying_prices: list[float] = Field(min_length=2, max_length=201)
    legs: list[PaperPayoffLegRequest] = Field(min_length=1, max_length=20)


class StrategyLegRequest(BaseModel):
    kind: str = Field(min_length=4, max_length=8)
    side: str = Field(min_length=3, max_length=4)
    entry_price: float = Field(..., ge=0)
    quantity: float = Field(..., gt=0)
    strike: float | None = Field(default=None, gt=0)
    multiplier: float = Field(1.0, gt=0)


class StrategyPayoffRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    underlying_prices: list[float] = Field(min_length=2, max_length=201)
    legs: list[StrategyLegRequest] = Field(min_length=1, max_length=20)


class CashFuturePayoffRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    cash_entry_price: float = Field(..., gt=0)
    future_entry_price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    underlying_prices: list[float] = Field(min_length=2, max_length=201)
    multiplier: float = Field(1.0, gt=0)


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


def _create_order(db: Session, *, user_id: int, symbol: str, side: str, price: float, quantity: float, pnl: float = 0.0) -> dict:
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
    return {"id": order.order_id, "symbol": order.symbol, "transaction_type": order.transaction_type, "price": price, "quantity": quantity, "status": order.status, "pnl": pnl}


@router.post("/paper/entry")
def paper_entry(request: PaperEntryRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    if _position(db, user_id) is not None:
        raise HTTPException(status_code=409, detail="A paper position is already active")
    account = _account(db, user_id)
    cost = _buy_cost(request.price, request.quantity)
    if account.virtual_balance < cost:
        raise HTTPException(status_code=400, detail="Insufficient paper balance")
    engine = DualExecutionEngine(_paper_fill, config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct))
    fill = engine.enter(request.price, request.quantity)
    state = engine.paper
    position = Position(user_id=user_id, symbol="PAPER", quantity=int(request.quantity) if request.quantity.is_integer() else round(request.quantity), average_price=state.entry_price, stop_loss=state.stop_loss, target=state.target)
    account.virtual_balance = round(account.virtual_balance - cost, 8)
    db.add(position)
    order = _create_order(db, user_id=user_id, symbol="PAPER", side="BUY", price=fill.price, quantity=fill.quantity)
    db.commit()
    return {"status":"success","mode":state.mode.value,"fill":{"price":fill.price,"quantity":fill.quantity},"entry_price":state.entry_price,"stop_loss":state.stop_loss,"target":state.target,"position":_position_payload(position),"order":order,"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}


@router.post("/paper/order")
def paper_order(request: PaperOrderRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
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
        engine = DualExecutionEngine(_paper_fill, config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct))
        fill = engine.enter(request.price, request.quantity)
        state = engine.paper
        active = Position(user_id=user_id, symbol=request.symbol, quantity=int(request.quantity) if request.quantity.is_integer() else round(request.quantity), average_price=state.entry_price, stop_loss=state.stop_loss, target=state.target)
        db.add(active)
        account.virtual_balance = round(account.virtual_balance - cost, 8)
        pnl = 0.0
        order = _create_order(db, user_id=user_id, symbol=request.symbol, side=side, price=fill.price, quantity=fill.quantity)
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
        order = _create_order(db, user_id=user_id, symbol=request.symbol, side=side, price=fill.price, quantity=fill.quantity, pnl=pnl)
    db.commit()
    remaining = None if side == "SELL" and active.quantity <= 0 else active
    return {"status":"success","mode":"paper","order":order,"position":_position_payload(remaining),"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}


@router.post("/paper/from-scanner")
def paper_from_scanner(request: ScannerPaperEntryRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    if not request.executable:
        raise HTTPException(status_code=409, detail="Scanner opportunity is not executable")
    if request.future_price is not None and request.future_price <= request.cash_price:
        raise HTTPException(status_code=409, detail="Scanner future price does not exceed cash price")
    if request.net_profit is not None and request.net_profit <= 0:
        raise HTTPException(status_code=409, detail="Scanner opportunity has no positive net profit")
    active = _position(db, user_id)
    if active is not None:
        raise HTTPException(status_code=409, detail="A paper position is already active")
    result = paper_order(PaperOrderRequest(symbol=request.symbol, transaction_type="BUY", price=request.cash_price, quantity=request.quantity, stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct), user_id=user_id, db=db)
    result["source"] = "cash-future-scanner"
    result["scanner_entry_price"] = request.cash_price
    result["scanner_future_price"] = request.future_price
    result["scanner_gap"] = request.gap
    result["scanner_net_profit"] = request.net_profit
    return result


@router.get("/paper/orders")
def paper_orders(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.user_id == user_id, Order.order_id.like(f"PAPER-{user_id}-%")).order_by(Order.id.asc()).all()
    return {"mode":"paper","orders":[{"id":item.order_id,"symbol":item.symbol,"transaction_type":item.transaction_type,"price":item.price,"quantity":float(item.quantity),"status":item.status,"pnl":float(item.pnl or 0.0)} for item in orders]}


@router.get("/paper/position")
def paper_position(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    position = _position(db, user_id)
    if position is None:
        return {"status":"flat","position":None,"mark_to_market":None}
    current_ltp = None
    quote_error = None
    if position.symbol and position.symbol.upper() != "PAPER":
        try:
            from app.market_data.client import MarketDataClient
            from app.market_data.instruments import InstrumentMaster
            symbol = position.symbol.strip().upper()
            instrument = InstrumentMaster().get_instrument(symbol, "NSE")
            if instrument:
                token = str(instrument.get("token", ""))
                if token:
                    response = MarketDataClient().ltp(exchange="NSE", tradingsymbol=symbol, symboltoken=token)
                    data = response.get("data") or {}
                    current_ltp = float(data["ltp"]) if data.get("ltp") is not None else None
        except Exception as exc:
            quote_error = str(exc)
    quantity = float(position.quantity)
    entry_price = float(position.average_price)
    mtm = None
    if current_ltp is not None:
        gross_pnl = round((current_ltp - entry_price) * quantity, 8)
        pnl_pct = round(((current_ltp - entry_price) / entry_price) * 100, 8) if entry_price else 0.0
        mtm = {"current_ltp": current_ltp, "gross_pnl": gross_pnl, "pnl_pct": pnl_pct, "charges": None, "net_pnl": None, "charges_status": "unavailable"}
    payload = {"status":"active","position":_position_payload(position),"mark_to_market":mtm}
    if quote_error:
        payload["quote_error"] = quote_error
    return payload


def _analytics_response(symbol: str, user_id: int, legs: tuple[PayoffLeg, ...], prices: tuple[float, ...]) -> dict:
    return {"status":"success","mode":"paper","symbol":symbol.upper(),"user_id":user_id,"legs":[{"kind":leg.kind,"side":leg.side,"strike":leg.strike,"entry_price":leg.entry_price,"quantity":leg.quantity,"multiplier":leg.multiplier} for leg in legs],"analytics":payoff_summary(legs, prices),"charges_status":"unavailable"}


@router.post("/paper/payoff")
def paper_payoff(request: PaperPayoffRequest, user_id: int = Depends(current_user_id)):
    """Return deterministic multi-leg payoff analytics for an authenticated paper analysis."""
    try:
        legs = tuple(PayoffLeg(kind=leg.kind, side=leg.side, strike=leg.strike, entry_price=leg.entry_price, quantity=leg.quantity, multiplier=leg.multiplier) for leg in request.legs)
        return _analytics_response(request.symbol, user_id, legs, tuple(request.underlying_prices))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/paper/payoff/from-strategy")
def paper_payoff_from_strategy(request: StrategyPayoffRequest, user_id: int = Depends(current_user_id)):
    """Build explicit strategy legs, then calculate the deterministic payoff."""
    try:
        inputs = tuple(StrategyLegInput(kind=leg.kind, side=leg.side, entry_price=leg.entry_price, quantity=leg.quantity, strike=leg.strike, multiplier=leg.multiplier) for leg in request.legs)
        legs = build_strategy_legs(inputs)
        return _analytics_response(request.symbol, user_id, legs, tuple(request.underlying_prices))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/paper/payoff/from-cash-future")
def paper_payoff_from_cash_future(request: CashFuturePayoffRequest, user_id: int = Depends(current_user_id)):
    """Build the scanner's cash/future strategy and return its payoff analytics."""
    try:
        legs = build_cash_future_strategy(cash_entry_price=request.cash_entry_price, future_entry_price=request.future_entry_price, quantity=request.quantity, multiplier=request.multiplier)
        return _analytics_response(request.symbol, user_id, legs, tuple(request.underlying_prices))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/paper/exit")
def paper_exit(request: PaperExitRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    account = _account(db, user_id)
    position = _position(db, user_id)
    if position is None:
        return {"status":"flat","position":None,"pnl":0.0,"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
    entry_price = float(position.average_price)
    quantity = float(position.quantity)
    pnl = round((request.price - entry_price) * quantity, 8)
    proceeds = _buy_cost(request.price, quantity)
    account.virtual_balance = round(account.virtual_balance + proceeds, 8)
    account.realized_pnl = round(account.realized_pnl + pnl, 8)
    order = _create_order(db, user_id=user_id, symbol=position.symbol, side="SELL", price=request.price, quantity=quantity, pnl=pnl)
    db.delete(position)
    db.commit()
    return {"status":"closed","entry_price":entry_price,"exit_price":request.price,"quantity":quantity,"pnl":pnl,"order":order,"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
