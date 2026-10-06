from datetime import datetime, timedelta
import time
from typing import Callable

from fastapi import APIRouter, Query

router = APIRouter(
    prefix="/api/v1/scanner/synthetic-cash-carry",
    tags=["Synthetic Cash-Carry"],
)
_latest_provider: Callable[[], tuple] | None = None


def configure(provider: Callable[[], tuple]) -> None:
    global _latest_provider
    _latest_provider = provider


def _serialize(result) -> dict:
    option, future = result.option, result.future
    return {
        "underlying": option.underlying,
        "instrument_class": option.instrument_class,
        "expiry": option.expiry,
        "strike": option.strike,
        "direction": result.direction,
        "timestamp_ns": option.timestamp_ns,
        "future_bid": future.bid,
        "future_ask": future.ask,
        "call_bid": option.call_bid,
        "call_ask": option.call_ask,
        "put_bid": option.put_bid,
        "put_ask": option.put_ask,
        "lot_size": future.lot_size,
        "executable_edge": result.executable_edge,
        "edge_per_lot": result.edge_per_lot,
        "gross_pnl": result.gross_pnl,
        "strike_distance": result.strike_distance,
        "strike_side": result.strike_side,
    }


@router.get("/live")
def live(limit: int = Query(50, ge=1, le=200), max_age_seconds: float = Query(5.0, gt=0, le=60)):
    results = _latest_provider() if _latest_provider is not None else ()
    cutoff = time.time_ns() - int(max_age_seconds * 1_000_000_000)
    results = tuple(r for r in results if int(getattr(r.option, "timestamp_ns", 0)) >= cutoff)
    rows = [_serialize(result) for result in results[:limit]]
    return {
        "status": "success",
        "scanner": "synthetic-cash-carry-live-1s",
        "mode": "paper-safe",
        "data": rows,
        "opportunity_count": sum(float(row["executable_edge"] or 0) > 0 for row in rows),
    }


@router.get("/alerts")
def alerts(days: int = Query(30, ge=1, le=30), limit: int = Query(500, ge=1, le=5000)):
    from app.core.database import SessionLocal
    from app.models.live_synthetic_alert_history import LiveSyntheticAlertHistory

    cutoff = datetime.utcnow() - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = (
            db.query(LiveSyntheticAlertHistory)
            .filter(LiveSyntheticAlertHistory.observed_at >= cutoff)
            .order_by(LiveSyntheticAlertHistory.observed_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "status": "success",
            "scanner": "synthetic-cash-carry",
            "mode": "live-alert-history",
            "days": days,
            "count": len(rows),
            "data": [
                {column.name: getattr(row, column.name) for column in LiveSyntheticAlertHistory.__table__.columns}
                for row in rows
            ],
        }
    finally:
        db.close()
