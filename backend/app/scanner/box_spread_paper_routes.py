"""Dedicated Box Spread paper-trading API. Never submits broker orders."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.database import SessionLocal
from app.market_data.instruments import InstrumentMaster
from app.market_data.box_spread_subscriptions import select_box_contracts
from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS
from app.scanner.box_spread_contract_resolver import (
    BoxSpreadContractResolutionError, resolve_box_spread_contracts,
)
from app.scanner.box_spread_paper_engine import BoxSpreadPaperEngine, BoxSpreadPaperEngineError
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperLedger, BoxSpreadPaperTradeRecord, BoxSpreadPaperLedgerEntry,
)

router = APIRouter(prefix="/box-spread-paper", tags=["box-spread-paper"])
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
    reference_prices: dict[str, float]
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
    if isinstance(exc, (ValueError, BoxSpreadContractResolutionError, BoxSpreadPaperEngineError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=503, detail="Instrument master or paper ledger is temporarily unavailable") from exc


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
            "next_step": "Submit four reference prices to the paper execution endpoint; no broker order is sent.",
        }
    except Exception as exc:
        _error(exc)


@router.post("/trades/{trade_id}/simulate-entry")
async def simulate_entry(trade_id: int, payload: SimulateEntry):
    try:
        with SessionLocal() as session:
            trade = session.get(BoxSpreadPaperTradeRecord, trade_id)
            if trade is None or trade.account_key != payload.account_key:
                raise HTTPException(status_code=404, detail="paper trade not found")
            request = CreatePaperTrade(
                underlying=trade.underlying, instrument_class=trade.instrument_class,
                expiry=trade.expiry, low_strike=float(trade.low_strike),
                high_strike=float(trade.high_strike), direction=trade.direction,
                lots=trade.lots, idempotency_key=trade.idempotency_key,
                account_key=payload.account_key,
            )
        contracts = _resolve(request)
        await _engine.create_intent(
            contracts=contracts, lots=request.lots, idempotency_key=request.idempotency_key,
        )
        execution = await _engine.execute_intent(
            idempotency_key=request.idempotency_key, reference_prices=payload.reference_prices,
        )
        receipts = []
        for fill in execution.fills:
            receipts.append(_ledger.record_fill(
                trade_id=trade_id, fill_id=f"{request.idempotency_key}:{fill.role}",
                role=fill.role, action=fill.action, quantity=fill.quantity,
                fill_price=fill.fill_price, account_key=payload.account_key,
            ))
        summary = _ledger.get_trade_summary(trade_id, account_key=payload.account_key)
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
