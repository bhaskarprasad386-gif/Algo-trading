"""Persistent paper-execution API boundary for the single local trading system.

Paper execution persists positions,
orders, virtual balance, and realized P&L in the application database.
Live broker execution remains disabled behind the broker safety layer.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.execution.dual_engine import DualExecutionEngine, ExecutionConfig, ExecutionMode, Fill
from app.execution.fill_accounting import ExecutedFill, FillAccountingState, apply_executed_fill
from app.execution.payoff import PayoffLeg, payoff_summary
from app.execution.strategy_legs import StrategyLegInput, build_cash_future_strategy, build_strategy_legs
from app.models import Order, Position, TradingAccount, User

PAPER_STARTING_BALANCE = 10_000_000.0

router = APIRouter(prefix="/api/v1/execution", tags=["Execution"])


class PaperEntryRequest(BaseModel):
    symbol: str = Field("PAPER", min_length=1, max_length=128)
    price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    stop_loss_pct: float = Field(0.02, ge=0)
    target_pct: float = Field(0.04, ge=0)
    fill_id: str | None = Field(default=None, min_length=1, max_length=128)


class PaperExitRequest(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=128)
    price: float = Field(..., gt=0)
    fill_id: str | None = Field(default=None, min_length=1, max_length=128)


class PaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=128)
    transaction_type: str = Field(min_length=3, max_length=4)
    price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    stop_loss_pct: float = Field(0.02, ge=0)
    target_pct: float = Field(0.04, ge=0)
    fill_id: str | None = Field(default=None, min_length=1, max_length=128)


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
    fill_id: str | None = Field(default=None, min_length=1, max_length=128)


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


def current_user_id(db: Session = Depends(get_db)) -> int:
    """Return the single local trading identity, failing closed on ambiguity.

    Until real authentication is wired into these legacy paper endpoints,
    account creation must also be safe when two first requests arrive at the
    same time. The database uniqueness constraint on TradingAccount.user_id is
    used as the serialization point for that bootstrap race.
    """
    accounts = (
        db.query(TradingAccount)
        .filter(
            TradingAccount.is_active.is_(True),
            TradingAccount.mode == "PAPER",
        )
        .order_by(TradingAccount.id.asc())
        .all()
    )
    if len(accounts) > 1:
        raise HTTPException(
            status_code=409,
            detail="multiple active paper trading accounts require authenticated user context",
        )
    if accounts:
        return int(accounts[0].user_id)

    user = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).first()
    if user is None:
        try:
            with db.begin_nested():
                user = User(
                    email="system@local.algo-trading",
                    hashed_password="",
                    full_name="Algo Trading System",
                    is_active=True,
                )
                db.add(user)
                db.flush()
        except IntegrityError:
            user = (
                db.query(User)
                .filter(User.email == "system@local.algo-trading")
                .first()
            )
            if user is None:
                raise HTTPException(
                    status_code=503,
                    detail="paper trading identity bootstrap unavailable",
                )
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=503,
                detail="paper trading identity bootstrap unavailable",
            )

    existing = (
        db.query(TradingAccount)
        .filter(
            TradingAccount.user_id == int(user.id),
            TradingAccount.mode == "PAPER",
        )
        .order_by(TradingAccount.id.asc())
        .first()
    )
    if existing is not None:
        if existing.is_active:
            return int(existing.user_id)
        raise HTTPException(
            status_code=409,
            detail="paper trading account exists but is inactive",
        )

    account = TradingAccount(
        user_id=user.id,
        mode="PAPER",
        virtual_balance=PAPER_STARTING_BALANCE,
        initial_virtual_balance=PAPER_STARTING_BALANCE,
        initial_balance_source="BOOTSTRAP",
        realized_pnl=0.0,
        is_active=True,
    )
    try:
        with db.begin_nested():
            db.add(account)
            db.flush()
    except IntegrityError:
        account = (
            db.query(TradingAccount)
            .filter(
                TradingAccount.user_id == int(user.id),
                TradingAccount.mode == "PAPER",
            )
            .order_by(TradingAccount.id.asc())
            .first()
        )
        if account is None or not account.is_active:
            raise HTTPException(
                status_code=503,
                detail="paper trading account bootstrap unavailable",
            )

    db.commit()
    db.refresh(account)
    return int(account.user_id)


def _paper_fill(mode: ExecutionMode, price: float, quantity: float) -> Fill:
    if mode is not ExecutionMode.PAPER:
        raise RuntimeError("paper endpoint cannot execute live orders")
    return Fill(price=price, quantity=quantity)


def _begin_paper_mutation(db: Session, user_id: int) -> TradingAccount:
    """Serialize account/position mutations for one paper-account transaction.

    SQLite is the production trading DB, so BEGIN IMMEDIATE acquires the
    database write lock before reading mutable balance/position state. On
    databases with row-lock support, SELECT ... FOR UPDATE provides the same
    serialization boundary.
    """
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        # current_user_id() may have opened a read transaction on this session.
        # Mutation locking must begin from a clean transaction boundary.
        db.rollback()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="paper trading account is busy; retry") from exc
        return _account(db, user_id)
    account = (
        db.query(TradingAccount)
        .filter(TradingAccount.user_id == user_id)
        .with_for_update()
        .first()
    )
    if account is None or not account.is_active:
        raise HTTPException(status_code=404, detail="Paper trading account not found")
    if account.mode.upper() != "PAPER":
        raise HTTPException(status_code=409, detail="Trading account is not in paper mode")
    return account


def _account(db: Session, user_id: int) -> TradingAccount:
    account = db.query(TradingAccount).filter(TradingAccount.user_id == user_id).first()
    if account is None or not account.is_active:
        raise HTTPException(status_code=404, detail="Paper trading account not found")
    if account.mode.upper() != "PAPER":
        raise HTTPException(status_code=409, detail="Trading account is not in paper mode")
    return account


def _position(db: Session, user_id: int, symbol: str | None = None) -> Position | None:
    query = db.query(Position).filter(
        Position.user_id == user_id,
        Position.is_paper.is_(True),
        Position.is_open.is_(True),
        Position.quantity != 0,
    )
    if symbol is not None:
        query = query.filter(Position.symbol == symbol.strip().upper())
    return query.order_by(Position.id.desc()).first()


def _validate_quantity(quantity: float) -> int:
    if not math.isfinite(float(quantity)) or quantity <= 0 or not float(quantity).is_integer():
        raise HTTPException(status_code=422, detail="quantity must be a positive integer")
    return int(quantity)


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


def _paper_audit_payload(*, user_id: int, symbol: str, side: str, quantity: int, price: float, pnl: float, fill_id: str | None, previous_hash: str | None) -> str:
    import hashlib
    import json
    payload = {
        "user_id": int(user_id),
        "symbol": symbol.strip().upper(),
        "side": side.upper(),
        "quantity": int(quantity),
        "price": round(float(price), 8),
        "pnl": round(float(pnl), 8),
        "fill_id": fill_id,
        "previous_audit_hash": previous_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _latest_paper_audit_hash(db: Session, user_id: int) -> str | None:
    order = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.is_paper.is_(True))
        .order_by(Order.id.desc())
        .first()
    )
    return order.audit_hash if order is not None else None


def _create_order(db: Session, *, user_id: int, symbol: str, side: str, price: float, quantity: float, pnl: float = 0.0, fill_id: str | None = None) -> dict:
    normalized_quantity = _validate_quantity(quantity)
    order_id = f"PAPER-{user_id}-{uuid.uuid4().hex[:16]}"
    previous_audit_hash = _latest_paper_audit_hash(db, user_id)
    audit_hash = _paper_audit_payload(user_id=user_id, symbol=symbol, side=side, quantity=normalized_quantity, price=price, pnl=pnl, fill_id=fill_id, previous_hash=previous_audit_hash)
    order = Order(
        order_id=order_id,
        symbol=symbol.strip().upper(),
        quantity=normalized_quantity,
        transaction_type=side,
        user_id=user_id,
        price=price,
        average_price=price,
        filled_quantity=normalized_quantity,
        average_fill_price=price,
        pnl=pnl,
        status="FILLED",
        is_paper=True,
        fill_id=fill_id,
        audit_hash=audit_hash,
        previous_audit_hash=previous_audit_hash,
    )
    db.add(order)
    db.flush()
    return {"id": order.order_id, "symbol": order.symbol, "transaction_type": order.transaction_type, "price": price, "quantity": normalized_quantity, "status": order.status, "pnl": pnl, "fill_id": fill_id}



def _validate_paper_state(db: Session, user_id: int) -> None:
    """Fail closed before commit if a paper-account invariant is violated."""
    account = _account(db, user_id)
    initial_virtual_balance = float(account.initial_virtual_balance)
    virtual_balance = float(account.virtual_balance)
    realized_pnl = float(account.realized_pnl or 0.0)
    if (
        not math.isfinite(initial_virtual_balance)
        or initial_virtual_balance <= 0
    ):
        raise RuntimeError("paper account initial_virtual_balance invariant violated")
    if not math.isfinite(virtual_balance) or virtual_balance < 0:
        raise RuntimeError("paper account virtual_balance invariant violated")
    if not math.isfinite(realized_pnl):
        raise RuntimeError("paper account realized_pnl invariant violated")

    paper_positions = (
        db.query(Position)
        .filter(Position.user_id == user_id, Position.is_paper.is_(True))
        .all()
    )
    seen_symbols: set[str] = set()
    for position in paper_positions:
        symbol = str(position.symbol or "").strip().upper()
        raw_symbol = str(position.symbol or "")
        raw_quantity = float(position.quantity or 0.0)
        raw_average_price = float(position.average_price or 0.0)
        if (
            not symbol
            or raw_symbol != symbol
            or not math.isfinite(raw_quantity)
            or not raw_quantity.is_integer()
            or raw_quantity == 0
            or not bool(position.is_open)
            or not math.isfinite(raw_average_price)
            or raw_average_price <= 0
        ):
            raise RuntimeError("paper position invariant violated")
        quantity = int(raw_quantity)
        average_price = raw_average_price
        if symbol in seen_symbols:
            raise RuntimeError("duplicate active paper position invariant violated")
        seen_symbols.add(symbol)

    paper_orders = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.is_paper.is_(True))
        .order_by(Order.id.asc())
        .all()
    )
    expected_audit_previous: str | None = None
    bootstrap_audit_required = str(account.initial_balance_source or "").strip().upper() == "BOOTSTRAP"
    for order in paper_orders:
        raw_quantity = float(order.quantity or 0.0)
        raw_filled_quantity = float(order.filled_quantity or 0.0)
        quantity = int(raw_quantity) if math.isfinite(raw_quantity) and raw_quantity.is_integer() else 0
        filled_quantity = int(raw_filled_quantity) if math.isfinite(raw_filled_quantity) and raw_filled_quantity.is_integer() else 0
        raw_symbol = str(order.symbol or "")
        raw_side = str(order.transaction_type or "")
        raw_fill_id = str(order.fill_id) if order.fill_id is not None else None
        price = float(order.price or 0.0)
        average_fill_price = float(order.average_fill_price or 0.0)
        average_price = float(order.average_price or 0.0)
        if (
            not math.isfinite(raw_quantity)
            or not raw_quantity.is_integer()
            or not math.isfinite(raw_filled_quantity)
            or not raw_filled_quantity.is_integer()
            or quantity <= 0
            or filled_quantity != quantity
            or not math.isfinite(price)
            or price <= 0
            or not math.isfinite(average_price)
            or average_price <= 0
            or abs(average_price - price) > 1e-8
            or not math.isfinite(average_fill_price)
            or average_fill_price <= 0
            or abs(average_fill_price - price) > 1e-8
            or raw_symbol != raw_symbol.strip().upper()
            or raw_side != raw_side.strip().upper()
            or (raw_fill_id is not None and raw_fill_id != raw_fill_id.strip())
            or order.status != "FILLED"
            or order.transaction_type.upper() not in {"BUY", "SELL"}
        ):
            raise RuntimeError("paper order invariant violated")
        if not math.isfinite(float(order.pnl or 0.0)):
            raise RuntimeError("paper order pnl invariant violated")

        audit_hash = str(order.audit_hash) if order.audit_hash is not None else None
        previous_audit_hash = str(order.previous_audit_hash) if order.previous_audit_hash is not None else None
        if bootstrap_audit_required and audit_hash is None:
            raise RuntimeError("paper order audit hash invariant violated")
        if audit_hash is not None:
            if len(audit_hash) != 64 or any(ch not in "0123456789abcdef" for ch in audit_hash.lower()):
                raise RuntimeError("paper order audit hash invariant violated")
            if previous_audit_hash != expected_audit_previous:
                raise RuntimeError("paper order audit chain invariant violated")
            expected_audit_hash = _paper_audit_payload(
                user_id=user_id,
                symbol=raw_symbol,
                side=raw_side,
                quantity=quantity,
                price=price,
                pnl=float(order.pnl or 0.0),
                fill_id=raw_fill_id,
                previous_hash=expected_audit_previous,
            )
            if audit_hash != expected_audit_hash:
                raise RuntimeError("paper order audit hash invariant violated")
            expected_audit_previous = audit_hash
        elif previous_audit_hash is not None:
            raise RuntimeError("paper order audit chain invariant violated")


def _normalized_fill_id(fill_id: str | None) -> str | None:
    if fill_id is None:
        return None
    value = fill_id.strip()
    if not value:
        raise HTTPException(status_code=422, detail="fill_id must not be blank")
    return value


def _existing_fill_order(db: Session, *, user_id: int, fill_id: str | None, symbol: str, side: str, price: float, quantity: int) -> dict | None:
    if fill_id is None:
        return None
    existing = db.query(Order).filter(Order.user_id == user_id, Order.fill_id == fill_id).first()
    if existing is None:
        return None
    if existing.symbol != symbol or existing.transaction_type.upper() != side or float(existing.price) != float(price) or int(existing.quantity) != int(quantity):
        raise HTTPException(status_code=409, detail="fill_id already exists with different execution details")
    return {"id": existing.order_id, "symbol": existing.symbol, "transaction_type": existing.transaction_type, "price": existing.price, "quantity": float(existing.quantity), "status": existing.status, "pnl": float(existing.pnl or 0.0), "fill_id": existing.fill_id}

def _accounting_after_fill(*, side: str, price: float, quantity: float, current_quantity: float, current_average_price: float, current_realized_pnl: float = 0.0) -> tuple[FillAccountingState, float]:
    before = FillAccountingState(quantity=current_quantity, average_price=current_average_price, realized_pnl=current_realized_pnl)
    after = apply_executed_fill(before, ExecutedFill(side=side, price=price, quantity=quantity))
    return after, round(after.realized_pnl - before.realized_pnl, 8)


def _repair_fingerprint_number(value) -> object:
    """Preserve malformed numeric state instead of silently truncating it in a repair fingerprint."""
    if value is None:
        return 0.0
    raw = float(value)
    if not math.isfinite(raw):
        return str(raw)
    return round(raw, 8)


def _paper_repair_precondition(db: Session, user_id: int, account: TradingAccount, orders: list[Order], positions: list[Position]) -> dict:
    """Return an immutable DB-state fingerprint for any future repair/apply path."""
    payload = {
        "user_id": int(user_id),
        "account": {
            "initial_virtual_balance": _repair_fingerprint_number(account.initial_virtual_balance),
            "initial_balance_source": str(account.initial_balance_source or ""),
            "mode": str(account.mode or ""),
            "is_active": bool(account.is_active),
            "virtual_balance": _repair_fingerprint_number(account.virtual_balance),
            "realized_pnl": _repair_fingerprint_number(account.realized_pnl),
        },
        "orders": [
            {
                "id": int(order.id),
                "fill_id": order.fill_id,
                "symbol": str(order.symbol or "").strip().upper(),
                "side": str(order.transaction_type or "").strip().upper(),
                "quantity": _repair_fingerprint_number(order.quantity),
                "filled_quantity": _repair_fingerprint_number(order.filled_quantity),
                "price": _repair_fingerprint_number(order.price),
                "average_price": _repair_fingerprint_number(order.average_price),
                "average_fill_price": _repair_fingerprint_number(order.average_fill_price),
                "pnl": _repair_fingerprint_number(order.pnl),
                "status": str(order.status or ""),
                "audit_hash": order.audit_hash,
                "previous_audit_hash": order.previous_audit_hash,
                "broker_order_id": order.broker_order_id,
                "is_paper": bool(order.is_paper),
            }
            for order in orders
        ],
        "positions": [
            {
                "id": int(position.id),
                "symbol": str(position.symbol or "").strip().upper(),
                "quantity": _repair_fingerprint_number(position.quantity),
                "average_price": _repair_fingerprint_number(position.average_price),
                "is_open": bool(position.is_open),
                "is_paper": bool(position.is_paper),
            }
            for position in sorted(positions, key=lambda item: int(item.id))
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return {
        "algorithm": "SHA256",
        "order_count": len(orders),
        "position_count": len(positions),
        "audit_head": orders[-1].audit_hash if orders else None,
        "state_hash": hashlib.sha256(canonical).hexdigest(),
    }


def _reconcile_paper_ledger(db: Session, user_id: int) -> dict:
    """Rebuild paper positions, realized P&L and cash from durable fills."""
    # SQLite legacy transaction mode does not necessarily begin a transaction for
    # SELECTs. Force a read snapshot when this helper is called outside an already
    # active transaction so account/orders/positions cannot drift mid-reconcile.
    if db.bind is not None and db.bind.dialect.name == "sqlite" and not db.in_transaction():
        db.connection().exec_driver_sql("BEGIN")
    account = _account(db, user_id)
    account_mode_canonicality_mismatch = str(account.mode or "") != "PAPER"
    account_source_canonicality_mismatch = str(account.initial_balance_source or "") != str(account.initial_balance_source or "").strip().upper()
    all_user_orders = (
        db.query(Order)
        .filter(Order.user_id == user_id)
        .order_by(Order.id.asc())
        .all()
    )
    orders = [order for order in all_user_orders if order.is_paper is True]
    rebuilt: dict[str, FillAccountingState] = {}
    initial_virtual_balance = float(account.initial_virtual_balance)
    stored_virtual_balance = float(account.virtual_balance)
    stored_realized_pnl = float(account.realized_pnl or 0.0)
    account_integrity: list[str] = []
    if not math.isfinite(initial_virtual_balance) or initial_virtual_balance <= 0:
        account_integrity.append("invalid_initial_virtual_balance")
    if not math.isfinite(stored_virtual_balance) or stored_virtual_balance < 0:
        account_integrity.append("invalid_virtual_balance")
    if not math.isfinite(stored_realized_pnl):
        account_integrity.append("invalid_realized_pnl")
    reconstructed_cash = initial_virtual_balance
    reconstructed_realized = 0.0
    seen_fill_ids: set[str] = set()
    invalid_orders: list[str] = []
    previous_hash: str | None = None

    for order in all_user_orders:
        if order.is_paper is not True and order.audit_hash:
            invalid_orders.append(f"paper_scope_mismatch:{order.id}")
    for order in orders:
        raw_order_id = str(order.order_id or "")
        expected_order_prefix = f"PAPER-{user_id}-"
        if not raw_order_id or not raw_order_id.startswith(expected_order_prefix) or raw_order_id != raw_order_id.strip():
            invalid_orders.append(f"order_identity_mismatch:{order.id}")
        symbol = str(order.symbol or "").strip().upper()
        side = str(order.transaction_type or "").strip().upper()
        raw_filled_quantity = float(order.filled_quantity or 0.0)
        raw_quantity = float(order.quantity or 0.0)
        raw_price = float(order.price) if order.price is not None else 0.0
        raw_average_price = float(order.average_price) if order.average_price is not None else 0.0
        raw_average_fill_price = float(order.average_fill_price) if order.average_fill_price is not None else 0.0
        raw_pnl = float(order.pnl) if order.pnl is not None else 0.0
        if (
            not symbol
            or side not in {"BUY", "SELL"}
            or not math.isfinite(raw_filled_quantity)
            or not raw_filled_quantity.is_integer()
            or raw_filled_quantity <= 0
            or not math.isfinite(raw_quantity)
            or not raw_quantity.is_integer()
            or raw_quantity <= 0
            or not math.isfinite(raw_price)
            or not math.isfinite(raw_average_price)
            or not math.isfinite(raw_average_fill_price)
            or not math.isfinite(raw_pnl)
            or raw_average_fill_price <= 0
            or str(order.status).upper() != "FILLED"
        ):
            invalid_orders.append(f"invalid_order:{order.id}")
            continue
        quantity = int(raw_filled_quantity)
        price = raw_average_fill_price or raw_price
        if str(order.status) != "FILLED":
            invalid_orders.append(f"order_status_canonicality_mismatch:{order.id}")
        if order.fill_id:
            fill_id = str(order.fill_id).strip()
            if fill_id in seen_fill_ids:
                invalid_orders.append(f"duplicate_fill_id:{fill_id}")
            seen_fill_ids.add(fill_id)
        if not order.audit_hash or order.previous_audit_hash != previous_hash:
            invalid_orders.append(f"audit_chain_mismatch:{order.id}")
        expected_hash = _paper_audit_payload(
            user_id=user_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            pnl=raw_pnl,
            fill_id=order.fill_id,
            previous_hash=previous_hash,
        )
        if order.audit_hash != expected_hash:
            invalid_orders.append(f"audit_hash_mismatch:{order.id}")
        previous_hash = order.audit_hash if order.audit_hash else previous_hash

        state = rebuilt.get(symbol, FillAccountingState())
        closed_qty = min(abs(int(state.quantity)), quantity) if (
            (state.quantity > 0 and side == "SELL") or
            (state.quantity < 0 and side == "BUY")
        ) else 0
        before_qty = int(state.quantity)
        before_avg = float(state.average_price)
        after, pnl_delta = _accounting_after_fill(
            side=side,
            price=price,
            quantity=quantity,
            current_quantity=state.quantity,
            current_average_price=state.average_price,
            current_realized_pnl=state.realized_pnl,
        )
        if abs(raw_pnl - pnl_delta) > 1e-8:
            invalid_orders.append(f"order_pnl_mismatch:{order.id}")
        if abs(float(order.average_price or 0.0) - float(order.price or 0.0)) > 1e-8:
            invalid_orders.append(f"order_average_price_mismatch:{order.id}")
        if abs(float(order.average_fill_price or 0.0) - float(order.price or 0.0)) > 1e-8:
            invalid_orders.append(f"order_average_fill_price_mismatch:{order.id}")
        if int(raw_quantity) != quantity:
            invalid_orders.append(f"order_quantity_mismatch:{order.id}")
        if str(order.symbol or "").strip() != symbol:
            invalid_orders.append(f"order_symbol_canonicality_mismatch:{order.id}")
        if str(order.transaction_type or "").strip().upper() != str(order.transaction_type or ""):
            invalid_orders.append(f"order_side_canonicality_mismatch:{order.id}")
        if order.fill_id is not None and str(order.fill_id).strip() != str(order.fill_id):
            invalid_orders.append(f"order_fill_id_canonicality_mismatch:{order.id}")

        if side == "BUY":
            if before_qty < 0:
                released_margin = _buy_cost(before_avg, closed_qty)
                remaining_long_cost = _buy_cost(price, int(after.quantity)) if after.quantity > 0 else 0.0
                reconstructed_cash = round(
                    reconstructed_cash + released_margin + pnl_delta - remaining_long_cost,
                    8,
                )
            else:
                reconstructed_cash = round(reconstructed_cash - _buy_cost(price, quantity), 8)
        else:
            if before_qty > 0:
                proceeds = _buy_cost(price, closed_qty)
                if after.quantity < 0:
                    reconstructed_cash = round(
                        reconstructed_cash + proceeds - _buy_cost(price, abs(int(after.quantity))),
                        8,
                    )
                else:
                    reconstructed_cash = round(reconstructed_cash + proceeds, 8)
            else:
                reconstructed_cash = round(
                    reconstructed_cash - _buy_cost(price, quantity),
                    8,
                )

        rebuilt[symbol] = after
        reconstructed_realized = after.realized_pnl + sum(
            state.realized_pnl for key, state in rebuilt.items() if key != symbol
        )

    all_paper_positions = (
        db.query(Position)
        .filter(Position.user_id == user_id, Position.is_paper.is_(True))
        .order_by(Position.id.asc())
        .all()
    )
    actual_positions: dict[str, Position] = {}
    duplicate_position_symbols: set[str] = set()
    position_symbol_canonicality_mismatches: list[int] = []
    position_state_mismatches: list[int] = []
    for position in all_paper_positions:
        raw_quantity = float(position.quantity or 0.0)
        raw_average_price = float(position.average_price or 0.0)
        if (
            not math.isfinite(raw_quantity)
            or not raw_quantity.is_integer()
            or not math.isfinite(raw_average_price)
            or (raw_quantity != 0 and raw_average_price <= 0)
        ):
            # Defer categorization until the shared mismatch list is built below.
            # The position is intentionally excluded from canonical comparison so
            # malformed values cannot be silently truncated or NaN-compared.
            continue
        if bool(position.is_open) != (int(raw_quantity) != 0):
            position_state_mismatches.append(int(position.id))
        if position.is_open and int(raw_quantity) != 0:
            raw_symbol = str(position.symbol or "")
            symbol = raw_symbol.strip().upper()
            if symbol:
                if raw_symbol != symbol:
                    position_symbol_canonicality_mismatches.append(int(position.id))
                if symbol in actual_positions:
                    duplicate_position_symbols.add(symbol)
                actual_positions[symbol] = position
    mismatches: list[str] = list(invalid_orders)
    mismatches.extend(account_integrity)
    if account_mode_canonicality_mismatch:
        mismatches.append("account_mode_canonicality_mismatch")
    if account_source_canonicality_mismatch:
        mismatches.append("account_source_canonicality_mismatch")
    malformed_position_ids = [
        int(position.id)
        for position in all_paper_positions
        if (
            not math.isfinite(float(position.quantity or 0.0))
            or not float(position.quantity or 0.0).is_integer()
            or not math.isfinite(float(position.average_price or 0.0))
            or (float(position.quantity or 0.0) != 0 and float(position.average_price or 0.0) <= 0)
        )
    ]
    mismatches.extend(f"invalid_position:{position_id}" for position_id in malformed_position_ids)
    mismatches.extend(
        f"position_symbol_canonicality_mismatch:{position_id}"
        for position_id in position_symbol_canonicality_mismatches
    )
    mismatches.extend(
        f"position_state_mismatch:{position_id}"
        for position_id in position_state_mismatches
    )
    mismatches.extend(f"duplicate_position:{symbol}" for symbol in sorted(duplicate_position_symbols))
    for symbol, state in rebuilt.items():
        if abs(state.quantity) > 0:
            position = actual_positions.pop(symbol, None)
            if position is None:
                mismatches.append(f"missing_position:{symbol}")
            elif int(position.quantity) != int(state.quantity) or abs(float(position.average_price) - state.average_price) > 1e-8:
                mismatches.append(f"position_mismatch:{symbol}")
        elif symbol in actual_positions:
            actual_positions.pop(symbol)
            mismatches.append(f"unexpected_position:{symbol}")
    for symbol in actual_positions:
        mismatches.append(f"orphan_position:{symbol}")

    reconstructed_realized = round(sum(state.realized_pnl for state in rebuilt.values()), 8)
    realized_delta = round(stored_realized_pnl - reconstructed_realized, 8) if math.isfinite(stored_realized_pnl) else 0.0
    balance_delta = round(stored_virtual_balance - reconstructed_cash, 8) if math.isfinite(stored_virtual_balance) and math.isfinite(reconstructed_cash) else 0.0
    if abs(realized_delta) > 1e-8:
        mismatches.append("realized_pnl_mismatch")
    if abs(balance_delta) > 1e-8:
        mismatches.append("virtual_balance_mismatch")

    source = str(account.initial_balance_source or "").strip().upper()
    if source != "BOOTSTRAP":
        baseline_status = source or "LEGACY_BASELINE"
    elif all(order.audit_hash for order in orders):
        baseline_status = "BOOTSTRAP"
    else:
        baseline_status = "LEGACY_UNFINGERPRINTED"
    mismatch_categories: set[str] = set()
    for mismatch in mismatches:
        if mismatch.startswith(("invalid_order:", "duplicate_fill_id:", "order_pnl_mismatch:", "order_average_price_mismatch:", "order_average_fill_price_mismatch:", "order_quantity_mismatch:", "order_symbol_canonicality_mismatch:", "order_side_canonicality_mismatch:", "order_fill_id_canonicality_mismatch:", "order_status_canonicality_mismatch:", "order_identity_mismatch:", "paper_scope_mismatch:")):
            mismatch_categories.add("ORDER_INTEGRITY")
        elif mismatch.startswith(("audit_chain_mismatch:", "audit_hash_mismatch:")):
            mismatch_categories.add("AUDIT_INTEGRITY")
        elif mismatch.startswith(("missing_position:", "position_mismatch:", "unexpected_position:", "orphan_position:", "invalid_position:", "duplicate_position:", "position_symbol_canonicality_mismatch:", "position_state_mismatch:")):
            mismatch_categories.add("POSITION_STATE")
        elif mismatch in {"account_mode_canonicality_mismatch", "account_source_canonicality_mismatch", "invalid_initial_virtual_balance", "invalid_virtual_balance", "invalid_realized_pnl", "realized_pnl_mismatch", "virtual_balance_mismatch"}:
            mismatch_categories.add("ACCOUNTING_STATE")
            mismatch_categories.add("ACCOUNTING_STATE")
        else:
            mismatch_categories.add("RECONCILIATION")

    if baseline_status != "BOOTSTRAP":
        mismatch_categories.add("BASELINE_INTEGRITY")

    if invalid_orders or baseline_status != "BOOTSTRAP":
        repairability = "BLOCKED"
    elif mismatches:
        repairability = "SAFE_DRY_RUN"
    else:
        repairability = "NONE"

    if repairability == "BLOCKED":
        repairability_reason = "ledger_or_baseline_integrity_failure"
    elif repairability == "SAFE_DRY_RUN":
        repairability_reason = "account_or_position_state_only"
    else:
        repairability_reason = "no_mismatch"

    precondition = _paper_repair_precondition(db, user_id, account, orders, all_paper_positions)

    return {
        "status": "OK" if not mismatches else "MISMATCH",
        "repairability": repairability,
        "repairability_reason": repairability_reason,
        "mismatch_categories": sorted(mismatch_categories),
        "baseline_status": baseline_status,
        "user_id": user_id,
        "orders": len(orders),
        "reconstructed_realized_pnl": reconstructed_realized,
        "stored_realized_pnl": round(float(account.realized_pnl or 0.0), 8),
        "reconstructed_virtual_balance": reconstructed_cash,
        "stored_virtual_balance": round(float(account.virtual_balance), 8),
        "reconstructed_positions": {
            symbol: {
                "quantity": int(state.quantity),
                "average_price": round(float(state.average_price), 8),
            }
            for symbol, state in rebuilt.items()
            if state.quantity != 0
        },
        "mismatches": mismatches,
        "repair_plan": {
            "apply": False,
            "reason": "read_only_dry_run",
            "precondition": precondition,
            "proposed_realized_pnl": reconstructed_realized,
            "proposed_virtual_balance": reconstructed_cash,
            "positions": {
                symbol: {
                    "quantity": int(state.quantity),
                    "average_price": round(float(state.average_price), 8),
                }
                for symbol, state in rebuilt.items()
                if state.quantity != 0
            },
        },
    }


@router.get("/paper/reconcile")
def paper_reconcile(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    return _reconcile_paper_ledger(db, user_id)


@router.post("/paper/entry")
def paper_entry(request: PaperEntryRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    quantity = _validate_quantity(request.quantity)
    symbol = request.symbol.strip().upper()
    fill_id = _normalized_fill_id(request.fill_id)
    account = _begin_paper_mutation(db, user_id)
    duplicate = _existing_fill_order(db, user_id=user_id, fill_id=fill_id, symbol=symbol, side="BUY", price=request.price, quantity=quantity)
    if duplicate is not None:
        return {"status":"success","mode":"paper","idempotent":True,"order":duplicate,"position":_position_payload(_position(db, user_id, symbol)),"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
    if _position(db, user_id, symbol) is not None:
        raise HTTPException(status_code=409, detail="A paper position is already active for this symbol")
    cost = _buy_cost(request.price, quantity)
    if account.virtual_balance < cost:
        raise HTTPException(status_code=400, detail="Insufficient paper balance")
    engine = DualExecutionEngine(_paper_fill, config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct))
    fill = engine.enter(request.price, quantity)
    state = engine.paper
    position = Position(user_id=user_id, symbol=symbol, quantity=quantity, average_price=state.entry_price, stop_loss=state.stop_loss, target=state.target)
    account.virtual_balance = round(account.virtual_balance - cost, 8)
    db.add(position)
    _validate_paper_state(db, user_id)
    order = _create_order(db, user_id=user_id, symbol=symbol, side="BUY", price=fill.price, quantity=fill.quantity, fill_id=fill_id)
    db.commit()
    return {"status":"success","mode":state.mode.value,"fill":{"price":fill.price,"quantity":fill.quantity},"entry_price":state.entry_price,"stop_loss":state.stop_loss,"target":state.target,"position":_position_payload(position),"order":order,"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}


@router.post("/paper/order")
def paper_order(request: PaperOrderRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    side = request.transaction_type.strip().upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="transaction_type must be BUY or SELL")
    quantity = _validate_quantity(request.quantity)
    symbol = request.symbol.strip().upper()
    fill_id = _normalized_fill_id(request.fill_id)
    account = _begin_paper_mutation(db, user_id)
    duplicate = _existing_fill_order(db, user_id=user_id, fill_id=fill_id, symbol=symbol, side=side, price=request.price, quantity=quantity)
    if duplicate is not None:
        return {"status":"success","mode":"paper","idempotent":True,"order":duplicate,"position":_position_payload(_position(db, user_id, symbol)),"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
    active = _position(db, user_id, symbol)
    remaining = active

    if side == "BUY":
        if active is not None and active.quantity > 0:
            raise HTTPException(status_code=409, detail="A paper position is already active for this symbol")
        if active is not None and active.quantity < 0:
            short_qty = abs(int(active.quantity))
            fill = Fill(price=request.price, quantity=quantity)
            accounting_state, pnl = _accounting_after_fill(side="BUY", price=fill.price, quantity=quantity, current_quantity=float(active.quantity), current_average_price=float(active.average_price), current_realized_pnl=account.realized_pnl)
            closed_qty = min(short_qty, quantity)
            margin_released = _buy_cost(active.average_price, closed_qty)
            cash_after_close = round(account.virtual_balance + margin_released + pnl, 8)
            remaining_qty = int(accounting_state.quantity)
            remaining_cost = _buy_cost(fill.price, remaining_qty) if remaining_qty > 0 else 0.0
            if cash_after_close < remaining_cost:
                raise HTTPException(status_code=400, detail="Insufficient paper balance for reversal long position")
            account.virtual_balance = round(cash_after_close - remaining_cost, 8)
            account.realized_pnl = accounting_state.realized_pnl
            if remaining_qty == 0:
                db.delete(active)
                remaining = None
            else:
                active.quantity = remaining_qty
                active.average_price = accounting_state.average_price
                db.flush()
                remaining = active
            order = _create_order(db, user_id=user_id, symbol=symbol, side=side, price=fill.price, quantity=fill.quantity, pnl=pnl, fill_id=fill_id)
        else:
            cost = _buy_cost(request.price, quantity)
            if account.virtual_balance < cost:
                raise HTTPException(status_code=400, detail="Insufficient paper balance")
            engine = DualExecutionEngine(_paper_fill, config=ExecutionConfig(stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct))
            fill = engine.enter(request.price, quantity)
            state = engine.paper
            accounting_state, _ = _accounting_after_fill(side="BUY", price=fill.price, quantity=fill.quantity, current_quantity=0, current_average_price=0, current_realized_pnl=account.realized_pnl)
            active = Position(user_id=user_id, symbol=symbol, quantity=quantity, average_price=accounting_state.average_price, stop_loss=state.stop_loss, target=state.target)
            remaining = active
            db.add(active)
            account.virtual_balance = round(account.virtual_balance - cost, 8)
            order = _create_order(db, user_id=user_id, symbol=symbol, side=side, price=fill.price, quantity=fill.quantity, fill_id=fill_id)
            pnl = 0.0
    else:
        if active is None:
            margin = _buy_cost(request.price, quantity)
            if account.virtual_balance < margin:
                raise HTTPException(status_code=400, detail="Insufficient paper balance for short margin")
            fill = Fill(price=request.price, quantity=quantity)
            accounting_state, pnl = _accounting_after_fill(side="SELL", price=fill.price, quantity=quantity, current_quantity=0, current_average_price=0, current_realized_pnl=account.realized_pnl)
            active = Position(user_id=user_id, symbol=symbol, quantity=-quantity, average_price=accounting_state.average_price, stop_loss=None, target=None)
            remaining = active
            db.add(active)
            account.virtual_balance = round(account.virtual_balance - margin, 8)
            order = _create_order(db, user_id=user_id, symbol=symbol, side=side, price=fill.price, quantity=fill.quantity, fill_id=fill_id)
        else:
            if active.quantity < 0:
                raise HTTPException(status_code=409, detail="Use BUY to cover the active short position")
            fill = Fill(price=request.price, quantity=quantity)
            accounting_state, pnl = _accounting_after_fill(side="SELL", price=fill.price, quantity=quantity, current_quantity=float(active.quantity), current_average_price=float(active.average_price), current_realized_pnl=account.realized_pnl)
            closed_qty = min(int(active.quantity), quantity)
            proceeds = _buy_cost(fill.price, closed_qty)
            if accounting_state.quantity < 0:
                remaining_short_qty = abs(int(accounting_state.quantity))
                margin = _buy_cost(fill.price, remaining_short_qty)
                account.virtual_balance = round(
                    account.virtual_balance + proceeds - margin,
                    8,
                )
            else:
                # Sale proceeds already include the economic value of the
                # closing fill. Realized P&L is ledger state, not additional
                # cash, so never add pnl on top of sale proceeds.
                account.virtual_balance = round(
                    account.virtual_balance + proceeds,
                    8,
                )
            account.realized_pnl = accounting_state.realized_pnl
            remaining_qty = int(accounting_state.quantity)
            if remaining_qty == 0:
                db.delete(active)
                remaining = None
            elif remaining_qty < 0:
                active.quantity = remaining_qty
                active.average_price = accounting_state.average_price
                active.stop_loss = None
                active.target = None
                db.flush()
                remaining = active
            else:
                active.quantity = remaining_qty
                remaining = active
            order = _create_order(db, user_id=user_id, symbol=symbol, side=side, price=fill.price, quantity=fill.quantity, pnl=pnl, fill_id=fill_id)
    _validate_paper_state(db, user_id)
    db.commit()
    return {"status":"success","mode":"paper","order":order,"position":_position_payload(remaining),"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}


@router.post("/paper/from-scanner")
def paper_from_scanner(request: ScannerPaperEntryRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    if not request.executable:
        raise HTTPException(status_code=409, detail="Scanner opportunity is not executable")
    if request.future_price is not None and request.future_price <= request.cash_price:
        raise HTTPException(status_code=409, detail="Scanner future price does not exceed cash price")
    if request.net_profit is not None and request.net_profit <= 0:
        raise HTTPException(status_code=409, detail="Scanner opportunity has no positive net profit")
    _validate_quantity(request.quantity)
    result = paper_order(PaperOrderRequest(symbol=request.symbol, transaction_type="BUY", price=request.cash_price, quantity=request.quantity, stop_loss_pct=request.stop_loss_pct, target_pct=request.target_pct, fill_id=request.fill_id), user_id=user_id, db=db)
    result["source"] = "cash-future-scanner"
    result["scanner_entry_price"] = request.cash_price
    result["scanner_future_price"] = request.future_price
    result["scanner_gap"] = request.gap
    result["scanner_net_profit"] = request.net_profit
    return result


@router.get("/paper/account")
def paper_account(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    """Return the lightweight paper-account summary used by the Home command center."""
    account = _account(db, user_id)
    open_positions = (
        db.query(Position)
        .filter(
            Position.user_id == user_id,
            Position.is_paper.is_(True),
            Position.is_open.is_(True),
            Position.quantity != 0,
        )
        .count()
    )
    return {
        "mode": "paper",
        "virtual_balance": float(account.virtual_balance),
        "realized_pnl": float(account.realized_pnl or 0.0),
        "open_positions": int(open_positions),
        "live_orders": "OFF",
    }


@router.get("/paper/orders")
def paper_orders(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.user_id == user_id, Order.order_id.like(f"PAPER-{user_id}-%")).order_by(Order.id.asc()).all()
    return {"mode":"paper","orders":[{"id":item.order_id,"symbol":item.symbol,"transaction_type":item.transaction_type,"price":item.price,"quantity":float(item.quantity),"status":item.status,"pnl":float(item.pnl or 0.0),"fill_id":item.fill_id} for item in orders]}


@router.get("/paper/position")
def paper_position(symbol: str | None = None, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    position = _position(db, user_id, symbol)
    if position is None:
        return {"status":"flat","position":None,"mark_to_market":None}
    current_ltp = None
    quote_error = None
    if position.symbol and position.symbol.upper() != "PAPER":
        try:
            from app.market_data.client import MarketDataClient
            from app.market_data.instruments import InstrumentMaster
            symbol_value = position.symbol.strip().upper()
            instrument = InstrumentMaster().get_instrument(symbol_value, "NSE")
            if instrument:
                token = str(instrument.get("token", ""))
                if token:
                    response = MarketDataClient().ltp(exchange="NSE", tradingsymbol=symbol_value, symboltoken=token)
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
    try:
        legs = tuple(PayoffLeg(kind=leg.kind, side=leg.side, strike=leg.strike, entry_price=leg.entry_price, quantity=leg.quantity, multiplier=leg.multiplier) for leg in request.legs)
        return _analytics_response(request.symbol, user_id, legs, tuple(request.underlying_prices))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/paper/payoff/from-strategy")
def paper_payoff_from_strategy(request: StrategyPayoffRequest, user_id: int = Depends(current_user_id)):
    try:
        inputs = tuple(StrategyLegInput(kind=leg.kind, side=leg.side, entry_price=leg.entry_price, quantity=leg.quantity, strike=leg.strike, multiplier=leg.multiplier) for leg in request.legs)
        legs = build_strategy_legs(inputs)
        return _analytics_response(request.symbol, user_id, legs, tuple(request.underlying_prices))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/paper/payoff/from-cash-future")
def paper_payoff_from_cash_future(request: CashFuturePayoffRequest, user_id: int = Depends(current_user_id)):
    try:
        legs = build_cash_future_strategy(cash_entry_price=request.cash_entry_price, future_entry_price=request.future_entry_price, quantity=request.quantity, multiplier=request.multiplier)
        return _analytics_response(request.symbol, user_id, legs, tuple(request.underlying_prices))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/paper/exit")
def paper_exit(request: PaperExitRequest, user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    account = _begin_paper_mutation(db, user_id)
    fill_id = _normalized_fill_id(request.fill_id)
    prior_fill = _fill_order_by_id(db, user_id=user_id, fill_id=fill_id)
    if prior_fill is not None:
        if (request.symbol is not None and prior_fill.symbol != request.symbol.strip().upper()) or float(prior_fill.price) != float(request.price):
            raise HTTPException(status_code=409, detail="fill_id already exists with different execution details")
        return {"status":"closed","idempotent":True,"quantity":float(prior_fill.quantity),"pnl":float(prior_fill.pnl or 0.0),"order":{"id":prior_fill.order_id,"symbol":prior_fill.symbol,"transaction_type":prior_fill.transaction_type,"price":prior_fill.price,"quantity":float(prior_fill.quantity),"status":prior_fill.status,"pnl":float(prior_fill.pnl or 0.0),"fill_id":prior_fill.fill_id},"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
    position = _position(db, user_id, request.symbol)
    if position is None:
        return {"status":"flat","position":None,"pnl":0.0,"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
    exit_side = "BUY" if position.quantity < 0 else "SELL"
    exit_quantity = abs(int(position.quantity))
    duplicate = _existing_fill_order(db, user_id=user_id, fill_id=fill_id, symbol=position.symbol, side=exit_side, price=request.price, quantity=exit_quantity)
    if duplicate is not None:
        return {"status":"closed","idempotent":True,"entry_price":float(position.average_price),"exit_price":request.price,"quantity":exit_quantity,"pnl":float(duplicate["pnl"]),"order":duplicate,"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
    entry_price = float(position.average_price)
    quantity = abs(int(position.quantity))
    if position.quantity < 0:
        fill = Fill(price=request.price, quantity=quantity)
        accounting_state, pnl = _accounting_after_fill(side="BUY", price=fill.price, quantity=quantity, current_quantity=float(position.quantity), current_average_price=entry_price, current_realized_pnl=account.realized_pnl)
        account.virtual_balance = round(account.virtual_balance + _buy_cost(entry_price, quantity) + pnl, 8)
        account.realized_pnl = accounting_state.realized_pnl
        side = "BUY"
    else:
        fill = Fill(price=request.price, quantity=quantity)
        accounting_state, pnl = _accounting_after_fill(side="SELL", price=fill.price, quantity=quantity, current_quantity=float(position.quantity), current_average_price=entry_price, current_realized_pnl=account.realized_pnl)
        account.virtual_balance = round(account.virtual_balance + _buy_cost(fill.price, quantity), 8)
        account.realized_pnl = accounting_state.realized_pnl
        side = "SELL"
    order = _create_order(db, user_id=user_id, symbol=position.symbol, side=side, price=fill.price, quantity=quantity, pnl=pnl, fill_id=fill_id)
    db.delete(position)
    _validate_paper_state(db, user_id)
    db.commit()
    return {"status":"closed","entry_price":entry_price,"exit_price":request.price,"quantity":quantity,"pnl":pnl,"order":order,"virtual_balance":account.virtual_balance,"realized_pnl":account.realized_pnl}
