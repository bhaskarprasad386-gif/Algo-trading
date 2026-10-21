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


def _normalize_expiry(value):
    """Serialize broker expiry values as ISO dates for every live API consumer."""
    text = str(value or "").strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    for fmt in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _deduplicate_latest_results(results: tuple) -> tuple:
    """Keep one newest result per contract/direction in a single live snapshot.

    A completed scan is one coherent snapshot. If a faulty provider supplies the
    same contract/direction more than once, retain the newest source timestamp;
    ties keep the higher-edge result. Different expiries, strikes, classes, and
    LONG/SHORT directions remain distinct.
    """
    latest = {}
    for result in results:
        option = getattr(result, "option", None)
        if option is None:
            continue
        try:
            timestamp_ns = int(getattr(option, "timestamp_ns", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            timestamp_ns = 0
        try:
            edge = float(getattr(result, "executable_edge", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            edge = 0.0
        raw_strike = getattr(option, "strike", "")
        try:
            numeric_strike = float(raw_strike)
            strike_key = format(numeric_strike, ".12g") if numeric_strike == numeric_strike else str(raw_strike).strip()
        except (TypeError, ValueError, OverflowError):
            strike_key = str(raw_strike or "").strip().upper()
        key = (
            str(getattr(option, "underlying", "") or "").strip().upper(),
            str(getattr(option, "instrument_class", "") or "").strip().upper(),
            str(_normalize_expiry(getattr(option, "expiry", "")) or ""),
            strike_key,
            str(getattr(result, "direction", "") or "").strip().upper(),
        )
        previous = latest.get(key)
        if previous is None:
            latest[key] = (timestamp_ns, edge, result)
        elif timestamp_ns > previous[0] or (timestamp_ns == previous[0] and edge > previous[1]):
            latest[key] = (timestamp_ns, edge, result)
    return tuple(item[2] for item in latest.values())


def _serialize(result) -> dict:
    option, future = result.option, result.future
    return {
        "underlying": option.underlying,
        "instrument_class": option.instrument_class,
        "expiry": _normalize_expiry(option.expiry),
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
    fresh_results = []
    for result in results:
        try:
            timestamp_ns = int(getattr(getattr(result, "option", None), "timestamp_ns", 0))
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        if timestamp_ns >= cutoff:
            fresh_results.append(result)
    results = tuple(fresh_results)
    results = _deduplicate_latest_results(tuple(results))
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
    # Aggregate existing counters into an operator-friendly diagnosis.
    # These are rejection/health events, not unique or mutually exclusive pair counts.
    blocker_prefixes = (
        "missing_ce_or_pe_leg",
        "missing_call_leg",
        "missing_put_leg",
        "missing_future_leg",
        "timestamp_mismatch",
        "expiry_mismatch",
        "invalid_expiry",
        "invalid_expiry_for_pricing",
        "invalid_strike",
        "instrument_class_mismatch",
        "invalid_future_instrument_class",
        "invalid_future_expiry",
        "invalid_future_timestamp",
        "invalid_future_lot_size",
        "illiquid_or_incomplete_option_leg",
        "lot_size_mismatch",
        "invalid_option_lot_size",
        "invalid_option_timestamp",
        "invalid_future_quote",
        "no_executable_ce_pe_pair",
        "waiting_for_executable_quote",
        "missing_atm_price",
        "atm_provider_errors",
        "monitor_atm_provider_errors",
        "config_provider_errors",
        "invalid_scanner_config",
        "scan_calculation_errors",
        "result_callback_errors",
        "invalid_payload_or_scope",
        "invalid_option_type",
        "invalid_option_instrument",
        "underlying_symbol_mismatch",
        "future_timestamp",
        "out_of_order_leg_updates",
        "invalid_quote",
        "stale_timestamp",
        "invalid_timestamp",
        "invalid_timestamp_unit",
    )
    blockers = [
        {"reason": key, "count": value}
        for key, value in counters.items()
        if (
            (key in blocker_prefixes or key.startswith("missing_atm_price_by_symbol:"))
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        )
    ]
    blockers.sort(key=lambda item: (-item["count"], item["reason"]))
    calculation_summary = {
        "evaluations": counters.get("scan_evaluations", 0),
        "successful_pairs": counters.get("successful_pairs", 0),
        "opportunities_emitted": counters.get("opportunities_emitted", 0),
        "empty_calculations": counters.get("empty_calculations", 0),
        "last_result_count": counters.get("last_result_count", 0),
    }
    return {
        "status": status,
        "scanner": "synthetic-cash-carry-live-1s",
        "running": running,
        "calculation_summary": calculation_summary,
        "top_blockers": blockers[:8],
        "blocker_counter_note": "Counters can overlap and are cumulative; they are diagnostic events, not unique pair totals.",
        "phase": snapshot.get("phase"),
        "phase_reason": snapshot.get("phase_reason"),
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
        "timestamp_ns", "underlying", "symbol", "expiry", "strike",
        "future_bid", "future_ask", "future_summary", "call_bid", "call_ask",
        "put_bid", "put_ask", "option_summary", "lot_size", "state", "reason",
        "direction", "executable_edge", "readiness_scope", "opportunity_confirmed",
        "qualification_status",
    )
    rows = []
    for source in source_rows[:limit]:
        if not isinstance(source, dict):
            continue
        row = {key: source.get(key) for key in allowed if key in source}
        # Older pair snapshots may call the underlying field "symbol". Normalize
        # it here so the UI never loses the name during sanitization.
        underlying = source.get("underlying") or source.get("symbol")
        if underlying:
            row["underlying"] = str(underlying).strip().upper()
        # Normalize common broker expiry representations to ISO YYYY-MM-DD.
        # Angel One and other sources may send YYYYMMDD, 29OCT2026, 29OCT26,
        # DD-MM-YYYY, or an already-normalized ISO date.
        expiry = source.get("expiry")
        if expiry is not None:
            expiry_text = str(expiry).strip().upper()
            if expiry_text.endswith(".0") and expiry_text[:-2].isdigit():
                expiry_text = expiry_text[:-2]
            normalized_expiry = None
            for expiry_format in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    normalized_expiry = datetime.strptime(expiry_text, expiry_format).date().isoformat()
                    break
                except ValueError:
                    continue
            row["expiry"] = normalized_expiry if normalized_expiry is not None else expiry
        rows.append(row)
    return {
        "status": str(snapshot.get("status") or "success"),
        "scanner": "synthetic-cash-carry-live-1s",
        "count": len(rows),
        "data": rows,
    }
