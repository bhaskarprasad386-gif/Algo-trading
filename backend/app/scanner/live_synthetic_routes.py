from datetime import datetime, timedelta
import time as _time
from typing import Annotated, Callable

from fastapi import APIRouter, Query

router = APIRouter(
    prefix="/api/v1/scanner/synthetic-cash-carry",
    tags=["Synthetic Cash-Carry"],
)
_latest_provider: Callable[[], tuple] | None = None
_diagnostics_provider: Callable[[], dict | None] | None = None
_pairs_provider: Callable[[], dict | None] | None = None


def configure(provider: Callable[[], tuple]) -> None:
    global _latest_provider
    _latest_provider = provider


def configure_diagnostics(provider: Callable[[], dict | None]) -> None:
    global _diagnostics_provider
    _diagnostics_provider = provider


def configure_pairs(provider: Callable[[], dict | None]) -> None:
    global _pairs_provider
    _pairs_provider = provider


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
def live(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    max_age_seconds: Annotated[float, Query(gt=0, le=60)] = 5.0,
):
    results = _latest_provider() if _latest_provider is not None else ()
    cutoff = _time.time_ns() - int(max_age_seconds * 1_000_000_000)
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



@router.get("/diagnostics")
def diagnostics():
    """Expose scanner health separately from common-feed ticks and opportunities."""
    snapshot = _diagnostics_provider() if _diagnostics_provider is not None else None
    if not isinstance(snapshot, dict):
        return {
            "status": "unavailable",
            "scanner": "synthetic-cash-carry-live-1s",
            "reason": "scanner diagnostics provider is not configured",
            "diagnostics": None,
        }
    option_recorder = snapshot.get("option_recorder")
    scanner = snapshot.get("scanner_diagnostics")
    counters = scanner.get("counters", {}) if isinstance(scanner, dict) else {}
    now_ns = _time.time_ns()
    last_observation_ns = counters.get("last_observation_at_ns")
    last_scan_ns = counters.get("last_successful_calculation_at_ns")
    def age_seconds(value):
        try:
            stamp = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return max(0.0, (now_ns - stamp) / 1_000_000_000) if stamp > 0 else None
    observation_age = age_seconds(last_observation_ns)
    scan_age = age_seconds(last_scan_ns)
    running = bool(snapshot.get("running"))
    if not running:
        status = "STOPPED"
    elif observation_age is None:
        status = "WAITING_FOR_OBSERVATIONS"
    elif observation_age > 5:
        status = "STALE"
    elif scan_age is None:
        status = "OBSERVING_NO_COMPLETE_SCAN"
    elif scan_age > 5:
        status = "NO_RECENT_COMPLETE_SCAN"
    else:
        status = "SCANNING"
    return {
        "status": status,
        "scanner": "synthetic-cash-carry-live-1s",
        "running": running,
        "target_count": snapshot.get("targets"),
        "scanner_initialized": bool(snapshot.get("scanner_initialized")),
        "observation_age_seconds": observation_age,
        "last_scan_age_seconds": scan_age,
        "last_observation_at_ns": last_observation_ns,
        "last_successful_calculation_at_ns": last_scan_ns,
        "last_result_count": counters.get("last_result_count", 0),
        "counters": counters,
        # Surface the actual underlying transport/consumer state. Without this,
        # WAITING_FOR_OBSERVATIONS cannot distinguish missing ATM ticks from a
        # dead worker, unresolved tokens, or a disconnected shared socket.
        "underlying_feed": snapshot.get("underlying_feed"),
        "option_recorder": {
            "running": option_recorder.get("running") if isinstance(option_recorder, dict) else None,
            "ticks_received": option_recorder.get("ticks_received") if isinstance(option_recorder, dict) else None,
            "errors": option_recorder.get("errors") if isinstance(option_recorder, dict) else None,
        },
    }


@router.get("/pairs")
def pairs(limit: Annotated[int, Query(ge=1, le=500)] = 200):
    """Return a bounded, sanitized live combo snapshot, including incomplete legs."""
    snapshot = _pairs_provider() if _pairs_provider is not None else None
    if not isinstance(snapshot, dict):
        return {
            "status": "unavailable",
            "scanner": "synthetic-cash-carry-live-1s",
            "count": 0,
            "data": [],
        }
    source_rows = snapshot.get("data")
    if not isinstance(source_rows, list):
        source_rows = []
    allowed = (
        "timestamp_ns", "underlying", "expiry", "strike",
        "future_bid", "future_ask", "call_bid", "call_ask",
        "put_bid", "put_ask", "lot_size", "state", "reason",
        "readiness_scope", "opportunity_confirmed", "qualification_status",
    )
    rows = [
        {key: row.get(key) for key in allowed}
        for row in source_rows[:limit]
        if isinstance(row, dict)
    ]
    return {
        "status": str(snapshot.get("status") or "success"),
        "scanner": "synthetic-cash-carry-live-1s",
        "count": len(rows),
        "data": rows,
    }
