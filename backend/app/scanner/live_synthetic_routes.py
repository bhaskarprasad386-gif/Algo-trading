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
_pair_history_last_saved: dict[str, float] = {}
_pair_history_last_cleanup = 0.0
_pair_history_last_error: str | None = None
_pair_history_last_saved_at: str | None = None
_pair_history_last_saved_count = 0


def configure(provider: Callable[[], tuple]) -> None:
    global _latest_provider
    _latest_provider = provider


def configure_diagnostics(provider: Callable[[], dict | None]) -> None:
    global _diagnostics_provider
    _diagnostics_provider = provider


def configure_pairs(provider: Callable[[], dict | None]) -> None:
    global _pairs_provider
    _pairs_provider = provider


def _synthetic_prices(row: dict) -> dict:
    """Return synthetic bid/ask only when all required real quotes exist."""
    def number(field):
        value = row.get(field)
        if value is None or isinstance(value, bool):
            return None
        try:
            result = float(value)
            return result if result == result and abs(result) != float("inf") else None
        except (TypeError, ValueError):
            return None

    strike = number("strike")
    call_bid, call_ask = number("call_bid"), number("call_ask")
    put_bid, put_ask = number("put_bid"), number("put_ask")
    return {
        "synthetic_bid": strike + call_bid - put_ask if strike is not None and call_bid is not None and put_ask is not None else None,
        "synthetic_ask": strike + call_ask - put_bid if strike is not None and call_ask is not None and put_bid is not None else None,
        "synthetic_formula": "strike + call - put",
    }


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


_LIVE_INDEX_MIN_EDGE = {
    "NIFTY": 5.0,
    "BANKNIFTY": 10.0,
    "FINNIFTY": 5.0,
    "MIDCPNIFTY": 3.0,
    "BANKEX": 10.0,
    "SENSEX": 10.0,
}


def _qualifies_live_edge(result) -> bool:
    """Defense-in-depth edge gate for rows exposed as live opportunities."""
    option = getattr(result, "option", None)
    if option is None:
        return False
    try:
        edge = float(getattr(result, "executable_edge", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    if not (edge > 0 and edge < float("inf")):
        return False

    symbol = str(getattr(option, "underlying", "") or "").strip().upper()
    instrument_class = str(getattr(option, "instrument_class", "") or "").strip().upper()
    if instrument_class == "STOCK":
        # Match the scanner's existing strict stock rule (> 5 points).
        return edge > 5.0
    minimum = _LIVE_INDEX_MIN_EDGE.get(symbol, 1.0)
    # Index thresholds are inclusive: edge equal to the minimum qualifies.
    return edge >= minimum


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


def _limit_results_fairly(results: tuple, limit: int) -> tuple:
    """Round-robin fresh results by underlying so one busy index cannot hide stocks."""
    grouped: dict[str, list] = {}
    for result in results:
        option = getattr(result, "option", None)
        if option is None:
            continue
        symbol = str(getattr(option, "underlying", "") or "").strip().upper() or "UNKNOWN"
        grouped.setdefault(symbol, []).append(result)
    for symbol_rows in grouped.values():
        def sort_key(result):
            try:
                stamp = int(getattr(getattr(result, "option", None), "timestamp_ns", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                stamp = 0
            try:
                edge = float(getattr(result, "executable_edge", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                edge = 0.0
            return stamp, edge
        symbol_rows.sort(key=sort_key, reverse=True)
    def newest_stamp(symbol):
        try:
            return int(getattr(getattr(grouped[symbol][0], "option", None), "timestamp_ns", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return 0
    symbols = sorted(grouped, key=lambda symbol: (newest_stamp(symbol), symbol), reverse=True)
    selected = []
    depth = 0
    while len(selected) < limit:
        added = False
        for symbol in symbols:
            if depth < len(grouped[symbol]):
                selected.append(grouped[symbol][depth])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        depth += 1
    return tuple(selected)


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
        **_synthetic_prices({
            "strike": option.strike,
            "call_bid": option.call_bid,
            "call_ask": option.call_ask,
            "put_bid": option.put_bid,
            "put_ask": option.put_ask,
        }),
        "lot_size": future.lot_size,
        "executable_edge": result.executable_edge,
        "edge_per_lot": result.edge_per_lot,
        "gross_pnl": result.gross_pnl,
        "carry_adjusted_synthetic_buy": getattr(result, "carry_adjusted_synthetic_buy", None),
        "carry_adjusted_synthetic_sell": getattr(result, "carry_adjusted_synthetic_sell", None),
        "carry_factor": getattr(result, "carry_factor", None),
        "fees_per_unit": getattr(result, "fees_per_unit", None),
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
        if timestamp_ns >= cutoff and _qualifies_live_edge(result):
            fresh_results.append(result)
    results = _deduplicate_latest_results(tuple(fresh_results))
    results = _limit_results_fairly(results, limit)
    rows = [_serialize(result) for result in results]
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


def _pair_readiness_summary(snapshot) -> dict:
    """Summarize structural pair blockers per underlying without exposing quote payloads."""
    if not isinstance(snapshot, dict):
        return {"available": False, "row_count": 0, "symbols": []}
    source_rows = snapshot.get("data")
    if not isinstance(source_rows, list):
        return {"available": True, "row_count": 0, "symbols": []}
    grouped = {}
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("underlying") or row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        item = grouped.setdefault(symbol, {
            "underlying": symbol, "row_count": 0, "ready_to_evaluate_count": 0,
            "incomplete_count": 0, "blocker_counts": {},
        })
        item["row_count"] += 1
        state = str(row.get("state") or "").strip().upper()
        if state == "READY_TO_EVALUATE":
            item["ready_to_evaluate_count"] += 1
        else:
            item["incomplete_count"] += 1
        reason = str(row.get("reason") or "").strip().upper()
        for blocker in (part.strip() for part in reason.split(",")):
            if not blocker or blocker == "AWAITING_SCANNER_QUALIFICATION":
                continue
            item["blocker_counts"][blocker] = item["blocker_counts"].get(blocker, 0) + 1
    symbols = []
    for symbol in sorted(grouped):
        item = grouped[symbol]
        item["blocker_counts"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(item["blocker_counts"].items(), key=lambda pair: (-pair[1], pair[0]))
        ]
        symbols.append(item)
    return {"available": True, "row_count": sum(item["row_count"] for item in symbols), "symbols": symbols}


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
    phase = str(snapshot.get("phase") or "").upper()
    # Outside market hours no fresh quote observations are expected. Surface
    # the runner phase before deriving observation health so the UI does not
    # incorrectly report WAITING_FOR_OBSERVATIONS before the session opens.
    if not running:
        status = "STOPPED"
    elif phase == "WAITING_FOR_MARKET_OPEN":
        status = "WAITING_FOR_MARKET_OPEN"
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
    pair_snapshot = _pairs_provider() if _pairs_provider is not None else None
    pair_readiness = _pair_readiness_summary(pair_snapshot)
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
        "pair_readiness_summary": pair_readiness,
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
        is_ready = bool(item.get("ready", item.get("atm_ready", False)))
        if is_ready:
            ready.append(underlying)
        else:
            not_ready.append({
                "underlying": underlying,
                "reason": item.get("reason") or ("underlying_price_unavailable" if not item.get("has_underlying_price", item.get("underlying_price_present", item.get("price_present", False))) else "atm_not_ready"),
                "concrete_strike_count": item.get("concrete_strike_count", item.get("strike_count", 0)),
                "underlying_price_present": bool(item.get("has_underlying_price", item.get("underlying_price_present", item.get("price_present", False)))),
                "underlying_price": item.get("last_underlying_price", item.get("underlying_price", item.get("price"))),
                "atm_strike": item.get("atm_strike"),
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
        # Contract identity and row/quote time used by both desktop and mobile pair monitors.
        "timestamp_ns", "observed_at", "underlying", "symbol", "instrument_class", "expiry", "strike",
        # Keep the actual quote legs visible; never replace missing prices with fabricated values.
        "future_bid", "future_ask", "future_summary", "call_bid", "call_ask", "put_bid", "put_ask",
        "option_summary", "lot_size",
        # Preserve the complete edge/direction context emitted by the scanner.
        "state", "reason", "direction", "executable_edge", "edge_per_lot", "gross_pnl",
        "strike_distance", "strike_side", "readiness_scope", "opportunity_confirmed",
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
        row.update(_synthetic_prices(row))
        rows.append(row)

    # Persist sampled real observations, including incomplete pairs, at a bounded rate.
    now_monotonic = _time.monotonic()
    try:
        from app.core.database import SessionLocal
        from app.models.live_synthetic_pair_history import LiveSyntheticPairHistory

        db = SessionLocal()
        try:
            history_rows = []
            pending_history_keys = []
            for row in rows:
                underlying = str(row.get("underlying") or "").strip().upper()
                if not underlying:
                    continue
                expiry_key = str(row.get("expiry") or "")
                strike_key = str(row.get("strike") if row.get("strike") is not None else "NO_STRIKE")
                key = "|".join((underlying, expiry_key, strike_key))
                if now_monotonic - _pair_history_last_saved.get(key, -1e9) < 5.0:
                    continue

                def _number(field):
                    value = row.get(field)
                    if value is None or isinstance(value, bool):
                        return None
                    try:
                        parsed = float(value)
                        return parsed if parsed == parsed and abs(parsed) != float("inf") else None
                    except (TypeError, ValueError):
                        return None

                raw_ts = row.get("timestamp_ns")
                try:
                    timestamp_ns = int(raw_ts) if raw_ts is not None else None
                except (TypeError, ValueError, OverflowError):
                    timestamp_ns = None
                lot_size = _number("lot_size")
                history_rows.append(LiveSyntheticPairHistory(
                    observed_at=datetime.utcnow(),
                    timestamp_ns=timestamp_ns,
                    underlying=underlying,
                    instrument_class=str(row.get("instrument_class") or "") or None,
                    expiry=expiry_key or None,
                    strike=_number("strike"),
                    future_bid=_number("future_bid"),
                    future_ask=_number("future_ask"),
                    call_bid=_number("call_bid"),
                    call_ask=_number("call_ask"),
                    put_bid=_number("put_bid"),
                    put_ask=_number("put_ask"),
                    lot_size=int(lot_size) if lot_size is not None else None,
                    state=str(row.get("state") or "") or None,
                    reason=str(row.get("reason") or "") or None,
                    direction=str(row.get("direction") or "") or None,
                    executable_edge=_number("executable_edge"),
                    edge_per_lot=_number("edge_per_lot"),
                ))
                pending_history_keys.append(key)
            if history_rows:
                db.add_all(history_rows)
            global _pair_history_last_cleanup
            if now_monotonic - _pair_history_last_cleanup > 300:
                db.query(LiveSyntheticPairHistory).filter(
                    LiveSyntheticPairHistory.observed_at < datetime.utcnow() - timedelta(days=2)
                ).delete(synchronize_session=False)
                _pair_history_last_cleanup = now_monotonic
            db.commit()
            for key in pending_history_keys:
                _pair_history_last_saved[key] = now_monotonic
            globals()["_pair_history_last_error"] = None
            globals()["_pair_history_last_saved_at"] = datetime.utcnow().isoformat() + "Z"
            globals()["_pair_history_last_saved_count"] = len(history_rows)
        finally:
            db.close()
    except Exception as exc:
        # History storage must not take down the live monitor endpoint.
        globals()["_pair_history_last_error"] = type(exc).__name__

    response = {
        "status": str(snapshot.get("status") or "success"),
        "scanner": "synthetic-cash-carry-live-1s",
        "count": len(rows),
        "data": rows,
    }
    # Forward only the scanner's bounded, pre-sanitized numeric summary. Never
    # pass through arbitrary provider metadata or raw broker payloads.
    summary = snapshot.get("summary")
    if isinstance(summary, dict):
        response["summary"] = {
            "total_rows": int(summary.get("total_rows") or 0),
            "symbols": summary.get("symbols") if isinstance(summary.get("symbols"), dict) else {},
            "blocker_counts": summary.get("blocker_counts") if isinstance(summary.get("blocker_counts"), dict) else {},
            "readiness_scope": str(summary.get("readiness_scope") or "STRUCTURAL_ONLY"),
            "opportunity_confirmed": bool(summary.get("opportunity_confirmed", False)),
        }
    return response


@router.get("/pair-history")
def pair_history(
    days: int = Query(1, ge=1, le=2),
    limit: int = Query(1000, ge=1, le=5000),
    underlying: str | None = Query(None, min_length=1, max_length=32),
):
    """Return durable sampled live-pair observations, including incomplete pairs."""
    from app.core.database import SessionLocal
    from app.models.live_synthetic_pair_history import LiveSyntheticPairHistory

    cutoff = datetime.utcnow() - timedelta(days=days)
    # FastAPI resolves Query defaults at request time; direct unit calls can pass
    # the Query object itself, so normalize only actual strings here.
    filter_symbol = underlying.strip().upper() if isinstance(underlying, str) and underlying.strip() else None
    db = SessionLocal()
    try:
        query = db.query(LiveSyntheticPairHistory).filter(
            LiveSyntheticPairHistory.observed_at >= cutoff
        )
        if filter_symbol:
            query = query.filter(LiveSyntheticPairHistory.underlying == filter_symbol)
        rows = query.order_by(LiveSyntheticPairHistory.observed_at.desc()).limit(limit).all()
        data = []
        for item in rows:
            payload = {
                "id": item.id,
                "timestamp_ns": item.timestamp_ns,
                "observed_at": item.observed_at.isoformat() + "Z",
                "underlying": item.underlying,
                "instrument_class": item.instrument_class,
                "expiry": item.expiry,
                "strike": item.strike,
                "future_bid": item.future_bid,
                "future_ask": item.future_ask,
                "call_bid": item.call_bid,
                "call_ask": item.call_ask,
                "put_bid": item.put_bid,
                "put_ask": item.put_ask,
                "lot_size": item.lot_size,
                "state": item.state,
                "reason": item.reason,
                "direction": item.direction,
                "executable_edge": item.executable_edge,
                "edge_per_lot": item.edge_per_lot,
                "readiness_scope": "HISTORICAL_OBSERVATION",
                "opportunity_confirmed": False,
            }
            payload.update(_synthetic_prices(payload))
            data.append(payload)
        return {
            "status": "success",
            "scanner": "synthetic-cash-carry-pair-history",
            "days": days,
            "underlying": filter_symbol,
            "count": len(data),
            "persistence": {
                "last_save_at": _pair_history_last_saved_at,
                "last_saved_count": _pair_history_last_saved_count,
                "last_error": _pair_history_last_error,
                "storage": "DATABASE",
            },
            "data": data,
        }
    finally:
        db.close()
