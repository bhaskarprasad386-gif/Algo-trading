from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.auto.live_paper import LivePaperTradeService, is_fresh_market_timestamp
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

def _refresh_marks(db: Session, trades):
    try:
        from app import main as runtime
        cash = runtime.live_cash_future_scanner.snapshot(max_age_seconds=5.0, limit=500)
        cash_map = {f"{x.get('symbol')}:{x.get('contract_month')}": x for x in cash}
        now_ns = runtime.time_module.time_ns()
        # Freshness helper rejects both stale and future-dated source timestamps.
        calendar = tuple(
            x for x in runtime.live_calendar_spread_scanner.snapshot(limit=500)
            if is_fresh_market_timestamp(getattr(x, "timestamp_ns", 0), now_ns)
        )
        cal_map = {f"{x.underlying}:{x.near_contract_month}:{x.far_contract_month}:{x.direction}": x for x in calendar}
        syn_map = {
            f"{x.option.underlying}:{x.option.expiry}:{x.option.strike:g}:{x.direction}": x
            for x in runtime.live_synthetic_latest_results
            if is_fresh_market_timestamp(getattr(x.option, "timestamp_ns", 0), now_ns)
        }
        box_map = {
            f"{x.low.underlying}:{x.low.expiry}:{x.low.strike:g}:{x.high.strike:g}:{x.direction}": x
            for x in runtime.live_box_spread_latest_results
            if is_fresh_market_timestamp(getattr(x.low, "timestamp_ns", 0), now_ns)
        }
        for trade in trades:
            if trade.strategy_id == "cash-future":
                row = cash_map.get(trade.event_id)
                edge = None if row is None else row.get("gap")
            elif trade.strategy_id == "calendar-spread":
                row = cal_map.get(trade.event_id)
                edge = None if row is None else row.gap_points
            elif trade.strategy_id == "synthetic-future-cash-carry":
                row = syn_map.get(trade.event_id)
                edge = None if row is None else row.executable_edge
            elif trade.strategy_id == "box-spread":
                row = box_map.get(trade.event_id)
                edge = None if row is None else row.executable_edge
            else:
                edge = None
            if edge is not None:
                from app import main as runtime
                pnl = runtime._executable_paper_pnl(trade, row)
                if pnl is not None:
                    service.mark(db, trade, edge=float(edge), pnl_override=pnl)
                else:
                    service.mark(db, trade, edge=float(edge), pnl_override=float(trade.unrealized_pnl))
        db.commit()
    except Exception:
        pass

@router.get("/status")
def status(db: Session = Depends(get_db)):
    user = current_user_id(db)
    service.close_expired(db)
    ongoing = service.ongoing(db, user)
    _refresh_marks(db, ongoing)
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
    user = current_user_id(db)
    closed = service.close_expired(db)
    ongoing = service.ongoing(db, user)
    _refresh_marks(db, ongoing)
    # A fresh mark can make an already-due trade visible to the second
    # expiry pass. Include both passes so the response does not under-report
    # trades closed during this refresh request.
    closed_after_mark = service.close_expired(db)
    # Expiry processing is global, but the API response must expose only
    # closures belonging to the current paper user.
    closed_ids = {
        trade.id for trade in closed
        if int(trade.user_id) == int(user)
    }
    closed_ids.update(
        trade.id for trade in closed_after_mark
        if int(trade.user_id) == int(user)
    )
    ongoing = service.ongoing(db, user)
    completed = service.completed(db, user)
    return {
        "closed_count": len(closed_ids),
        "ongoing": [payload(x) for x in ongoing],
        "completed": [payload(x) for x in completed],
        "live_orders": False,
    }

@router.post("/positions/{position_id}/close")
def close(position_id: int, db: Session = Depends(get_db)):
    user = current_user_id(db)
    # Resolve an already-reached expiry before accepting a manual close. This
    # makes the expiry/manual-close race deterministic: expiry wins once due.
    service.close_expired(db)
    trade = db.query(LivePaperTrade).filter(
        LivePaperTrade.id == position_id,
        LivePaperTrade.user_id == user,
        LivePaperTrade.status == "ONGOING",
    ).first()
    if not trade:
        raise HTTPException(404, "live paper trade not found")
    # Manual close must use the freshest executable mark available. If quotes
    # are stale/missing, _refresh_marks preserves the last valid executable P&L.
    _refresh_marks(db, [trade])
    return {"status": "success", "trade": payload(service.close(db, trade, "MANUAL"))}

@router.get("/positions/{position_id}")
def detail(position_id: int, db: Session = Depends(get_db)):
    user = current_user_id(db)
    # Keep the single-position response consistent with /status and /refresh:
    # resolve due expiry before exposing the position state.
    service.close_expired(db)
    trade = db.query(LivePaperTrade).filter(
        LivePaperTrade.id == position_id, LivePaperTrade.user_id == user
    ).first()
    if not trade:
        raise HTTPException(404, "live paper trade not found")
    if trade.status == "ONGOING":
        # Detail should expose the same latest executable P&L mark as the
        # collection endpoints, while preserving the last valid mark when
        # fresh executable quotes are unavailable.
        _refresh_marks(db, [trade])
        trade = db.query(LivePaperTrade).filter(
            LivePaperTrade.id == position_id, LivePaperTrade.user_id == user
        ).first()
        if not trade:
            raise HTTPException(404, "live paper trade not found")
    return payload(trade)
