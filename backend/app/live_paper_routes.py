from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.auto.live_paper import LivePaperTradeService
from app.execution.paper_routes import current_user_id
from app.models.live_paper_trade import LivePaperTrade

router = APIRouter(prefix="/api/v1/live-paper", tags=["Live Paper Trading"])
service = LivePaperTradeService()

def payload(p):
    return {
        "id": p.id, "strategy": p.strategy_id, "symbol": p.symbol,
        "event_id": p.event_id, "direction": p.direction,
        "expiry": p.expiry, "earliest_expiry": p.earliest_expiry,
        "lot_size": p.lot_size, "lots": p.lots,
        "entry_edge": p.entry_edge, "current_edge": p.current_edge,
        "capital_used": p.capital_used,
        "unrealized_pnl": p.unrealized_pnl, "realized_pnl": p.realized_pnl,
        "pnl_pct": p.pnl_pct, "legs": __import__("json").loads(p.legs_json or "[]"),
        "status": p.status, "exit_reason": p.exit_reason,
        "opened_at": p.opened_at, "closed_at": p.closed_at,
        "last_mark_at": p.last_mark_at,
    }

@router.get("/status")
def status(db: Session = Depends(get_db)):
    user = current_user_id(db)
    service.close_expired(db)
    ongoing = service.ongoing(db, user)
    completed = service.completed(db, user)
    return {
        "live_orders": False,
        "ongoing": [payload(x) for x in ongoing],
        "completed": [payload(x) for x in completed],
        "ongoing_count": len(ongoing), "completed_count": len(completed),
        "ongoing_pnl": round(sum(x.unrealized_pnl for x in ongoing), 8),
        "completed_pnl": round(sum(x.realized_pnl for x in completed), 8),
    }

@router.post("/refresh")
def refresh(db: Session = Depends(get_db)):
    # The strategy runners remain the source of live marks. This endpoint is
    # intentionally a thin lifecycle hook; it only enforces expiry closure.
    user = current_user_id(db)
    closed = service.close_expired(db)
    ongoing = service.ongoing(db, user)
    completed = service.completed(db, user)
    return {
        "closed_count": len(closed),
        "ongoing": [payload(x) for x in ongoing],
        "completed": [payload(x) for x in completed],
        "live_orders": False,
    }

@router.post("/positions/{position_id}/close")
def close(position_id: int, db: Session = Depends(get_db)):
    user = current_user_id(db)
    trade = db.query(LivePaperTrade).filter(
        LivePaperTrade.id == position_id,
        LivePaperTrade.user_id == user,
        LivePaperTrade.status == "ONGOING",
    ).first()
    if not trade:
        raise HTTPException(404, "live paper trade not found")
    return {"status": "success", "trade": payload(service.close(db, trade, "MANUAL"))}

@router.get("/positions/{position_id}")
def detail(position_id: int, db: Session = Depends(get_db)):
    user = current_user_id(db)
    trade = db.query(LivePaperTrade).filter(
        LivePaperTrade.id == position_id, LivePaperTrade.user_id == user
    ).first()
    if not trade:
        raise HTTPException(404, "live paper trade not found")
    return payload(trade)
