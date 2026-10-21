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
    """Keep one newest result per contract/direction in a single live snapshot."""
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
        if previous is None or timestamp_ns > previous[0] or (timestamp_ns == previous[0] and edge > previous[1]):
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
    results = _deduplicate_latest_results(tuple(fresh_results))
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
    """Expose scanner health, per-symbol subscription, and ATM-chain readiness."""
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

    blocker_prefixes = (
        "missing_ce_or_pe_leg", "missing_call_leg", "missing_put_leg", "missing_future_leg",
        "timestamp_mismatch", "expiry_mismatch", "invalid_expiry", "invalid_expiry_for_pricing",
        "invalid_strike", "instrument_class_mismatch", "invalid_future_instrument_class",
        "invalid_future_expiry", "invalid_future_timestamp", "illiquid_or_incomplete_option_leg",
        "lot_size_mismatch", "invalid_option_lot_size", "invalid_option_timestamp",
        "invalid_future_quote", "no_executable_ce_pe_pair", "waiting_for_executable_quote",
        "missing_atm_price", "atm_provider_errors", "monitor_atm_provider_errors",
        "config_provider_errors", "invalid_scanner_config", "scan_calculation_errors",
        "result_callback_errors", "invalid_payload_or_scope", "invalid_option_type",
        "invalid_option_instrument", "underlying_symbol_mismatch", "future_timestamp",
        "out_of_order_leg_updates", "invalid_quote", "stale_timestamp", "invalid_timestamp",
        "invalid_timestamp_unit",
    )
    blockers = [
        {"reason": key, "count": value}
        for key, value in counters.items()
        if ((key in blocker_prefixes or key.startswith("missing_atm_price_by_symbol:"))
            and isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0)
    ]
    blockers.sort(key=lambda item: (-item["count"], item["reason"]))
    calculation_summary = {
        "evaluations": counters.get("scan_evaluations", 0),
        "successful_pairs": counters.get("successful_pairs", 0),
        "opportunities_emitted": counters.get("opportunities_emitted", 0),
        "empty_calculations": counters.get("empty_calculations", 0),
        "last_result_count": counters.get("last_result_count", 0),
    }
    atm_tracker = snapshot.get("atm_tracker_diagnostics")
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
        "subscription_diagnostics": snapshot.get("subscription_diagnostics"),
        "atm_tracker_diagnostics": atm_tracker,
        "atm_readiness_summary": _atm_readiness_summary(atm_tracker),
        "observation_age_seconds": observation_age,
        "last_scan_age_seconds": scan_age,
        "last_observation_at_ns": last_observation_ns,
        "last_successful_calculation_at_ns": last_scan_ns,
        "last_result_count": counters.get("last_result_count", 0),
        "counters": counters,
        "underlying_feed": snapshot.get("underlying_feed"),
        "option_recorder": {
            "running": option_recorder.get("running") if isinstance(option_recorder, dict) else None,
            "ticks_received": option_recorder.get("ticks_received") if isinstance(option_recorder, dict) else None,
            "errors": option_recorder.get("errors") if isinstance(option_recorder, dict) else None,
        },
    }


def _atm_readiness_summary(atm_tracker) -> dict:
    """Return a compact, UI-safe summary of concrete ATM-chain readiness."""
    if not isinstance(atm_tracker, dict):
        return {"available": False, "tracked_symbols": 0, "ready_symbols": 0, "not_ready_symbols": []}
    symbols = atm_tracker.get("symbols")
    if not isinstance(symbols, list):
        return {"available": True, "tracked_symbols": 0, "ready_symbols": 0, "not_ready_symbols": []}
    ready = []
    not_ready = []
    for item in symbols:
        if not isinstance(item, dict):
            continue
        underlying = str(item.get("underlying") or "").strip().upper()
        if not underlying:
            continue
        if bool(item.get("ready")):
            ready.append(underlying)
        else:
            not_ready.append({
                "underlying": underlying,
                "reason": item.get("reason") or "atm_not_ready",
                "concrete_strike_count": item.get("concrete_strike_count", item.get("strike_count", 0)),
                "underlying_price_present": bool(item.get("underlying_price_present", item.get("price_present", False))),
                "source_age_seconds": item.get("source_age_seconds"),
            })
    return {
        "available": True,
        "tracked_symbols": len(ready) + len(not_ready),
        "ready_symbols": len(ready),
        "ready_underlyings": sorted(ready),
        "not_ready_symbols": sorted(not_ready, key=lambda row: row["underlying"]),
        "symbols_without_concrete_chain": sorted({
            str(symbol).strip().upper()
            for symbol in (atm_tracker.get("symbols_without_concrete_chain") or [])
            if str(symbol).strip()
        }),
    }


@router.get("/pairs")
def pairs(limit: Annotated[int, Query(ge=1, le=500)] = 200):
    """Return a bounded, sanitized live combo snapshot, including incomplete legs."""
    snapshot = _pairs_provider() if _pairs_provider is not None else None
    if not isinstance(snapshot, dict):
        return {"status": "unavailable", "scanner": "synthetic-cash-carry-live-1s", "count": 0, "data": []}
    source_rows = snapshot.get("data")
    if not isinstance(source_rows, list):
        source_rows = []
    allowed = (
        "timestamp_ns", "underlying", "symbol", "expiry", "strike", "future_bid", "future_ask",
        "future_summary", "call_bid", "call_ask", "put_bid", "put_ask", "option_summary", "lot_size",
        "state", "reason", "direction", "executable_edge", "readiness_scope", "opportunity_confirmed",
        "qualification_status",
    )
    rows = []
    for source in source_rows[:limit]:
        if not isinstance(source, dict):
            continue
        row = {key: source.get(key) for key in allowed if key in source}
        underlying = source.get("underlying") or source.get("symbol")
        if underlying:
            row["underlying"] = str(underlying).strip().upper()
        expiry = source.get("expiry")
        if expiry is not None:
            normalized_expiry = _normalize_expiry(expiry)
            row["expiry"] = normalized_expiry
        rows.append(row)
    return {"status": str(snapshot.get("status") or "success"), "scanner": "synthetic-cash-carry-live-1s", "count": len(rows), "data": rows}
