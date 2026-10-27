"""Dedicated Box Spread paper-trading API. Never submits broker orders."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.database import SessionLocal
from app.market_data.instruments import InstrumentMaster
from app.market_data.box_spread_subscriptions import select_box_contracts
from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS
from app.scanner.live_box_spread_routes import live as live_box_spread_opportunities
from app.scanner.box_spread_contract_resolver import (
    BoxSpreadContractResolutionError, resolve_box_spread_contracts,
)
from app.scanner.box_spread_paper_engine import BoxSpreadPaperEngine, BoxSpreadPaperEngineError
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperLedger, BoxSpreadPaperTradeRecord, BoxSpreadPaperLedgerEntry,
)

router = APIRouter(prefix="/api/v1/scanner/box-spread-paper", tags=["box-spread-paper"])
_engine = BoxSpreadPaperEngine()
_master = InstrumentMaster()
_ledger = BoxSpreadPaperLedger(SessionLocal)


class CreatePaperTrade(BaseModel):
    underlying: str = Field(min_length=1, max_length=64)
    instrument_class: str
    expiry: str
    low_strike: float = Field(gt=0)
    high_strike: float = Field(gt=0)
    direction: str
    lots: int = Field(ge=1, le=100)
    idempotency_key: str = Field(min_length=1, max_length=100)
    account_key: str = Field(default="default", min_length=1, max_length=128)


class SimulateEntry(BaseModel):
    # Accepted for client compatibility, but never trusted as the execution price source.
    reference_prices: dict[str, float] | None = None
    account_key: str = Field(default="default", min_length=1, max_length=128)


class ExpirySettlement(BaseModel):
    settlement_date: date
    settlement_prices: dict[str, float]
    account_key: str = Field(default="default", min_length=1, max_length=128)


class ExitLeg(BaseModel):
    role: str
    quantity: int = Field(ge=1)
    fill_price: float = Field(ge=0)
    fill_id: str = Field(min_length=1, max_length=96)
    account_key: str = Field(default="default", min_length=1, max_length=128)


def _resolve(payload: CreatePaperTrade):
    if not _master.instruments:
        _master.download()
    cls = payload.instrument_class.strip().upper()
    allowed_stocks = frozenset(str(x).upper() for x in TOP15_LIQUID_STOCK_SYMBOLS)
    selection = select_box_contracts(
        _master,
        underlying=payload.underlying,
        instrument_class=cls,
        atm_strike=(payload.low_strike + payload.high_strike) / 2,
        expiry=payload.expiry,
        allowed_stock_symbols=allowed_stocks,
    )
    return resolve_box_spread_contracts(
        subscriptions=selection.subscriptions,
        underlying=payload.underlying,
        instrument_class=cls,
        expiry=payload.expiry,
        low_strike=payload.low_strike,
        high_strike=payload.high_strike,
        direction=payload.direction,
    )


def _error(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (ValueError, LookupError, BoxSpreadContractResolutionError, BoxSpreadPaperEngineError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=503, detail="Instrument master or paper ledger is temporarily unavailable") from exc


def _canonical_expiry(value: object) -> str:
    text = str(value or "").strip().upper()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d%b%Y", "%d%b%y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _reference_prices_for_direction(row: dict[str, Any], direction: str) -> dict[str, float]:
    """Select executable quote sides consistent with the four paper leg actions."""
    if direction == "LONG":
        return {
            "LOW_CE": row["low_call_ask"],
            "HIGH_CE": row["high_call_bid"],
            "HIGH_PE": row["high_put_ask"],
            "LOW_PE": row["low_put_bid"],
        }
    if direction == "SHORT":
        return {
            "LOW_CE": row["low_call_bid"],
            "HIGH_CE": row["high_call_ask"],
            "HIGH_PE": row["high_put_bid"],
            "LOW_PE": row["low_put_ask"],
        }
    raise ValueError("direction must be LONG or SHORT")


def _contract_payload(contracts) -> list[dict[str, Any]]:
    return [{
        "role": leg.role, "exchange_type": leg.exchange_type, "token": leg.token,
        "symbol": leg.symbol, "underlying": leg.underlying, "expiry": leg.expiry,
        "option_type": leg.option_type, "strike": leg.strike, "lot_size": leg.lot_size,
    } for leg in contracts.legs]


@router.get("/account")
def account(account_key: str = "default"):
    try:
        balance = _ledger.ensure_account(account_key)
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False,
                "account_key": account_key, "virtual_balance": str(balance)}
    except Exception as exc:
        _error(exc)


@router.get("/trades")
def trades(account_key: str = "default", limit: int = 100):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    try:
        with SessionLocal() as session:
            rows = session.scalars(select(BoxSpreadPaperTradeRecord)
                .where(BoxSpreadPaperTradeRecord.account_key == account_key)
                .order_by(BoxSpreadPaperTradeRecord.created_at.desc())
                .limit(limit)).all()
            return {"mode": "PAPER_ONLY", "broker_orders_enabled": False, "count": len(rows),
                    "data": [{
                        "trade_id": row.id, "underlying": row.underlying,
                        "instrument_class": row.instrument_class, "expiry": row.expiry,
                        "low_strike": float(row.low_strike), "high_strike": float(row.high_strike),
                        "direction": row.direction, "lots": row.lots, "quantity": row.quantity,
                        "lot_size": row.lot_size, "status": row.status,
                        "created_at": row.created_at.isoformat(),
                        "fee_schedule_version": row.fee_schedule_version,
                    } for row in rows]}
    except Exception as exc:
        _error(exc)


@router.post("/trades")
def create_trade(payload: CreatePaperTrade):
    try:
        contracts = _resolve(payload)
        trade = _ledger.create_trade(
            idempotency_key=payload.idempotency_key, underlying=contracts.underlying,
            instrument_class=contracts.instrument_class, expiry=contracts.expiry,
            low_strike=contracts.low_strike, high_strike=contracts.high_strike,
            direction=contracts.direction, lots=payload.lots, lot_size=contracts.lot_size,
            account_key=payload.account_key,
        )
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "trade_id": trade.id, "status": trade.status,
            "idempotency_key": trade.idempotency_key,
            "contracts": _contract_payload(contracts),
            "next_step": "Run paper simulation against fresh backend Box Spread quotes; no broker order is sent.",
        }
    except Exception as exc:
        _error(exc)


def _load_trade_request(trade_id: int, account_key: str) -> CreatePaperTrade:
    with SessionLocal() as session:
        trade = session.get(BoxSpreadPaperTradeRecord, trade_id)
        if trade is None or trade.account_key != account_key:
            raise HTTPException(status_code=404, detail="paper trade not found")
        return CreatePaperTrade(
            underlying=trade.underlying, instrument_class=trade.instrument_class,
            expiry=trade.expiry, low_strike=float(trade.low_strike),
            high_strike=float(trade.high_strike), direction=trade.direction,
            lots=trade.lots, idempotency_key=trade.idempotency_key,
            account_key=account_key,
        )


@router.post("/trades/{trade_id}/simulate-entry")
async def simulate_entry(trade_id: int, payload: SimulateEntry):
    try:
        request = await asyncio.to_thread(_load_trade_request, trade_id, payload.account_key)
        # Instrument-master refresh, scanner snapshot reads, and SQL writes are
        # synchronous dependencies; keep them off the ASGI event loop.
        contracts = await asyncio.to_thread(_resolve, request)
        live_snapshot = await asyncio.to_thread(
            live_box_spread_opportunities, limit=500, max_age_seconds=5.0
        )
        current_row = next((
            row for row in live_snapshot.get("data", [])
            if str(row.get("symbol", "")).strip().upper() == contracts.underlying
            and str(row.get("instrument_class", "")).strip().upper() == contracts.instrument_class
            and _canonical_expiry(row.get("expiry_date") or row.get("expiry")) == _canonical_expiry(contracts.expiry)
            and abs(float(row.get("low_strike", 0)) - contracts.low_strike) < 0.0001
            and abs(float(row.get("high_strike", 0)) - contracts.high_strike) < 0.0001
            and str(row.get("direction", "")).strip().upper() == contracts.direction
        ), None)
        if current_row is None:
            raise HTTPException(status_code=409, detail="No matching fresh live Box Spread opportunity; paper execution was not started")
        # Never trust prices posted by the browser. Use the current backend scanner
        # row, which has already passed the five-second four-leg freshness check.
        # Map executable bid/ask sides by each paper leg action. Never feed a
        # sell-side quote into a simulated BUY (or vice versa).
        reference_prices = _reference_prices_for_direction(current_row, contracts.direction)
        await _engine.create_intent(
            contracts=contracts, lots=request.lots, idempotency_key=request.idempotency_key,
        )
        execution = await _engine.execute_intent(
            idempotency_key=request.idempotency_key, reference_prices=reference_prices,
        )
        receipts = []
        for fill in execution.fills:
            receipts.append(await asyncio.to_thread(
                _ledger.record_fill,
                trade_id=trade_id, fill_id=f"{request.idempotency_key}:{fill.role}",
                role=fill.role, action=fill.action, quantity=fill.quantity,
                fill_price=fill.fill_price, account_key=payload.account_key,
            ))
        summary = await asyncio.to_thread(
            _ledger.get_trade_summary, trade_id, account_key=payload.account_key
        )
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "trade_id": trade_id, "status": summary["status"],
            "latency_seconds": execution.latency_seconds,
            "slippage_rate": str(execution.slippage_rate),
            "fills": [{
                "role": fill.role, "action": fill.action, "quantity": fill.quantity,
                "reference_price": str(fill.reference_price), "fill_price": str(fill.fill_price),
                "slippage": str(fill.slippage), "notional": str(fill.notional),
                "charges": str(receipt.charges), "virtual_balance": str(receipt.virtual_balance),
            } for fill, receipt in zip(execution.fills, receipts, strict=True)],
            "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
        }
    except HTTPException:
        raise
    except Exception as exc:
        _error(exc)


@router.post("/trades/{trade_id}/exit")
def exit_leg(trade_id: int, payload: ExitLeg):
    try:
        receipt = _ledger.record_exit_fill(
            trade_id=trade_id, fill_id=payload.fill_id, role=payload.role,
            quantity=payload.quantity, fill_price=payload.fill_price,
            account_key=payload.account_key,
        )
        summary = _ledger.get_trade_summary(trade_id, account_key=payload.account_key)
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "receipt": {
                "trade_id": receipt.trade_id, "fill_id": receipt.fill_id,
                "role": receipt.role, "action": receipt.action,
                "quantity": receipt.quantity, "fill_price": str(receipt.fill_price),
                "charges": str(receipt.charges), "cash_delta": str(receipt.cash_delta),
                "virtual_balance": str(receipt.virtual_balance),
            },
            "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
        }
    except Exception as exc:
        _error(exc)


@router.post("/trades/{trade_id}/settle-expiry")
def settle_expiry(trade_id: int, payload: ExpirySettlement):
    try:
        result = _ledger.settle_expiry(
            trade_id=trade_id, settlement_prices=payload.settlement_prices,
            settlement_date=payload.settlement_date, account_key=payload.account_key,
        )
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "trade_id": trade_id, "settlement_date": result["settlement_date"],
            "settled_legs": result["settled_legs"], "status": result["status"],
            "receipts": [{
                "role": receipt.role, "action": receipt.action,
                "quantity": receipt.quantity, "fill_price": str(receipt.fill_price),
                "charges": str(receipt.charges), "cash_delta": str(receipt.cash_delta),
                "virtual_balance": str(receipt.virtual_balance),
            } for receipt in result["receipts"]],
        }
    except Exception as exc:
        _error(exc)


@router.get("/trades/{trade_id}/ledger")
def trade_ledger(trade_id: int, account_key: str = "default"):
    try:
        summary = _ledger.get_trade_summary(trade_id, account_key=account_key)
        with SessionLocal() as session:
            rows = session.scalars(select(BoxSpreadPaperLedgerEntry)
                .where(BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                       BoxSpreadPaperLedgerEntry.account_key == account_key)
                .order_by(BoxSpreadPaperLedgerEntry.created_at, BoxSpreadPaperLedgerEntry.id)).all()
            entries = [{
                "fill_id": row.fill_id, "role": row.role, "action": row.action,
                "quantity": row.quantity, "fill_price": str(row.fill_price),
                "notional": str(row.notional), "charges": str(row.charges),
                "cash_delta": str(row.cash_delta), "created_at": row.created_at.isoformat(),
            } for row in rows]
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False,
                "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
                "entries": entries}
    except Exception as exc:
        _error(exc)


__all__ = ["router"]
