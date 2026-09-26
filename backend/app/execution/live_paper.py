"""Broker-independent live paper execution driven by market-data ticks.

This module never sends broker orders. It evaluates paper orders against the
latest market tick, persists lifecycle state, and updates positions/MTM.
Broker connectivity can be attached later without changing the paper contract.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import Order, Position, TradingAccount


@dataclass(frozen=True)
class PaperQuote:
    symbol: str
    ltp: float
    bid: float | None = None
    ask: float | None = None
    timestamp: datetime | None = None

    @classmethod
    def from_tick(cls, tick: dict[str, Any]) -> "PaperQuote":
        symbol = str(tick.get("symbol") or tick.get("tradingSymbol") or "").strip().upper()
        if not symbol:
            raise ValueError("tick requires symbol")
        raw_ltp = tick.get("ltp", tick.get("last_traded_price"))
        if raw_ltp is None:
            raise ValueError("tick requires ltp")
        ltp = float(raw_ltp)
        if not math.isfinite(ltp) or ltp <= 0:
            raise ValueError("tick ltp must be positive and finite")

        def num(*keys: str) -> float | None:
            for key in keys:
                value = tick.get(key)
                if value is None:
                    continue
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value > 0:
                    return value
            return None

        return cls(symbol=symbol, ltp=ltp, bid=num("bid", "best_bid", "best_bid_price"),
                   ask=num("ask", "best_ask", "best_ask_price"),
                   timestamp=datetime.now(timezone.utc))


class LivePaperExecution:
    """Persistent paper order lifecycle evaluated only from supplied market data."""

    VALID_TYPES = {"MARKET", "LIMIT", "SL"}
    VALID_SIDES = {"BUY", "SELL"}

    def place(self, db: Session, *, user_id: int, symbol: str, side: str,
              quantity: int, order_type: str = "MARKET", price: float | None = None,
              trigger_price: float | None = None, client_order_id: str | None = None) -> Order:
        account = self._account(db, user_id)
        if account.mode.upper() != "PAPER":
            raise ValueError("live paper execution requires PAPER account mode")
        symbol, side, order_type = symbol.strip().upper(), side.strip().upper(), order_type.strip().upper()
        if not symbol or side not in self.VALID_SIDES or order_type not in self.VALID_TYPES:
            raise ValueError("invalid symbol, side, or order_type")
        if int(quantity) <= 0 or int(quantity) != quantity:
            raise ValueError("quantity must be a positive integer")
        quantity = int(quantity)
        if order_type == "LIMIT" and not (price and price > 0):
            raise ValueError("LIMIT order requires positive price")
        if order_type == "SL" and not (trigger_price and trigger_price > 0):
            raise ValueError("SL order requires positive trigger_price")
        existing = (db.query(Order).filter(Order.user_id == user_id, Order.order_id == client_order_id).first()
                    if client_order_id else None)
        if existing is not None:
            return existing
        order = Order(order_id=client_order_id or f"LIVE-PAPER-{user_id}-{uuid.uuid4().hex[:16]}",
                      symbol=symbol, quantity=quantity, transaction_type=side, status="OPEN",
                      user_id=user_id, price=price, pnl=0.0, order_type=order_type,
                      trigger_price=trigger_price, filled_quantity=0, average_fill_price=None,
                      time_in_force="DAY")
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    def on_tick(self, db: Session, *, user_id: int, tick: dict[str, Any]) -> list[dict[str, Any]]:
        quote = PaperQuote.from_tick(tick)
        orders = (db.query(Order).filter(Order.user_id == user_id, Order.symbol == quote.symbol,
                                          Order.status == "OPEN").order_by(Order.id.asc()).all())
        fills = []
        for order in orders:
            fill_price = self._fill_price(order, quote)
            if fill_price is not None:
                fills.append(self._fill(db, user_id=user_id, order=order, price=fill_price))
        if fills:
            db.commit()
        return fills

    def mark_to_market(self, db: Session, *, user_id: int, symbol: str, price: float) -> dict[str, float]:
        if not math.isfinite(float(price)) or price <= 0:
            raise ValueError("price must be positive and finite")
        position = (db.query(Position).filter(Position.user_id == user_id,
                    Position.symbol == symbol.strip().upper(), Position.quantity != 0)
                    .order_by(Position.id.desc()).first())
        if position is None:
            return {"quantity": 0.0, "price": float(price), "unrealized_pnl": 0.0}
        qty = float(position.quantity)
        return {"quantity": qty, "price": float(price),
                "unrealized_pnl": round((float(price) - float(position.average_price)) * qty, 8)}

    def cancel(self, db: Session, *, user_id: int, order_id: str) -> Order:
        order = db.query(Order).filter(Order.user_id == user_id, Order.order_id == order_id).first()
        if order is None:
            raise ValueError("order not found")
        if order.status == "OPEN":
            order.status = "CANCELLED"
            db.commit()
            db.refresh(order)
        return order

    @staticmethod
    def _account(db: Session, user_id: int) -> TradingAccount:
        account = db.query(TradingAccount).filter(TradingAccount.user_id == user_id).first()
        if account is None or not account.is_active:
            raise ValueError("paper trading account not found")
        return account

    @classmethod
    def _fill_price(cls, order: Order, quote: PaperQuote) -> float | None:
        side, order_type = order.transaction_type.upper(), (order.order_type or "MARKET").upper()
        if order_type == "MARKET":
            return quote.ask if side == "BUY" and quote.ask else quote.bid if side == "SELL" and quote.bid else quote.ltp
        if order_type == "LIMIT":
            if side == "BUY" and quote.ask is not None and quote.ask <= float(order.price):
                return quote.ask
            if side == "SELL" and quote.bid is not None and quote.bid >= float(order.price):
                return quote.bid
            return None
        trigger = float(order.trigger_price)
        if side == "BUY" and quote.ask is not None and quote.ask >= trigger:
            return quote.ask
        if side == "SELL" and quote.bid is not None and quote.bid <= trigger:
            return quote.bid
        return None

    def _fill(self, db: Session, *, user_id: int, order: Order, price: float) -> dict[str, Any]:
        qty, side = int(order.quantity), order.transaction_type.upper()
        account = self._account(db, user_id)
        position = (db.query(Position).filter(Position.user_id == user_id, Position.symbol == order.symbol,
                    Position.quantity != 0).order_by(Position.id.desc()).first())
        if side == "BUY":
            cost = price * qty
            if account.virtual_balance < cost and position is None:
                order.status = "REJECTED"
                return {"order_id": order.order_id, "status": "REJECTED", "reason": "INSUFFICIENT_BALANCE"}
            if position is None:
                position = Position(user_id=user_id, symbol=order.symbol, quantity=qty, average_price=price)
                db.add(position)
            elif position.quantity > 0:
                total = position.quantity + qty
                position.average_price = ((position.average_price * position.quantity) + cost) / total
                position.quantity = total
            else:
                position.quantity += qty
            account.virtual_balance = round(account.virtual_balance - cost, 8)
        else:
            if position is None or position.quantity < qty:
                order.status = "REJECTED"
                return {"order_id": order.order_id, "status": "REJECTED", "reason": "INSUFFICIENT_POSITION"}
            pnl = (price - position.average_price) * qty
            account.realized_pnl = round(account.realized_pnl + pnl, 8)
            account.virtual_balance = round(account.virtual_balance + price * qty, 8)
            position.quantity -= qty
            if position.quantity == 0:
                db.delete(position)
        order.status, order.filled_quantity, order.average_fill_price, order.price = "FILLED", qty, price, price
        return {"order_id": order.order_id, "status": "FILLED", "symbol": order.symbol,
                "side": side, "quantity": qty, "price": price}
