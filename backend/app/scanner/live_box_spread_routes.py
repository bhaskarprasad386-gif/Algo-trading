"""HTTP access to live Box Spread opportunities and durable alerts."""
from typing import Annotated
from types import SimpleNamespace
import json
from fastapi import APIRouter, Query
import time
from app.models import LiveBoxSpreadAlertHistory, LiveBoxSpreadSnapshot
from app.core.database import SessionLocal

router=APIRouter(prefix="/api/v1/scanner/box-spread",tags=["box-spread"])
_latest=()
_diagnostics_getter = None
_spot_provider = None
_index_selection_provider = None
_monitor_getter = None
_expiry_types = {}
MAX_LEG_TIMESTAMP_SKEW_NS = 8_000_000_000

def configure_spot_provider(provider):
    """Configure a source-backed, freshness-checked underlying spot getter."""
    global _spot_provider
    _spot_provider = provider


def configure_index_selection_provider(provider):
    """Configure the persisted manual index universe for spot snapshots."""
    global _index_selection_provider
    _index_selection_provider = provider

def _spot_fields(symbol):
    # Spot is supplementary context: a spot-feed failure must never suppress
    # an otherwise valid four-leg option opportunity.
    try:
        snapshot = _spot_provider(symbol) if callable(_spot_provider) else None
    except Exception:
        snapshot = None
    if not isinstance(snapshot, dict):
        return {"spot_price": None, "spot_timestamp_ns": None, "spot_age_seconds": None}
    return {
        "spot_price": snapshot.get("spot_price"),
        "spot_timestamp_ns": snapshot.get("spot_timestamp_ns"),
        "spot_age_seconds": snapshot.get("spot_age_seconds"),
    }

def _expiry_date(value):
    from datetime import datetime
    text = str(value or "").strip().upper()
    for fmt in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d %B %Y", "%d %B %y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def _expiry_key(value):
    """Canonicalize expiry representations across instrument-master and API formats."""
    parsed = _expiry_date(value)
    if parsed:
        return parsed
    return "".join(character for character in str(value or "").strip().upper() if character.isalnum())

def _timestamp_ns(value):
    from decimal import Decimal, InvalidOperation
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number > 0 and number == number.to_integral_value() else None
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def _leg_timestamps(row):
    """Return exact positive timestamps for each executable option quote."""
    low_ts = _timestamp_ns(getattr(row.low, "timestamp_ns", None))
    high_ts = _timestamp_ns(getattr(row.high, "timestamp_ns", None))
    if low_ts is None or high_ts is None:
        return (None, None, None, None)
    values = (
        getattr(row.low, "call_timestamp_ns", None),
        getattr(row.low, "put_timestamp_ns", None),
        getattr(row.high, "call_timestamp_ns", None),
        getattr(row.high, "put_timestamp_ns", None),
    )
    return tuple(
        _timestamp_ns(value if value is not None else fallback)
        for value, fallback in zip(values, (low_ts, low_ts, high_ts, high_ts))
    )


def configure(getter):
    global _latest; _latest=getter

def configure_monitor(getter):
    """Configure fresh raw-threshold quote results for open-position rules."""
    global _monitor_getter
    _monitor_getter = getter

def configure_diagnostics(getter):
    """Configure a read-only getter for the active Box Spread scanner diagnostics."""
    global _diagnostics_getter
    _diagnostics_getter = getter

@router.get("/diagnostics")
def diagnostics():
    """Expose quote-assembly rejection reasons without raw quotes or account data."""
    snapshot = _diagnostics_getter() if callable(_diagnostics_getter) else None
    if not isinstance(snapshot, dict):
        snapshot = {"counts": {}, "last_scan": {}, "active_buckets": 0}
    return {"strategy": "box-spread", "status": "available" if snapshot.get("last_scan") else "waiting", **snapshot}

def set_expiry_types(expiry_types):
    """Set expiry classifications derived from the current option instrument master."""
    global _expiry_types
    _expiry_types = {
        (str(symbol).strip().upper(), _expiry_key(expiry)): str(kind).strip().upper()
        for (symbol, expiry), kind in (expiry_types or {}).items()
    }


def classify_expiry_type(symbol, expiry):
    """Return the currently known expiry type for a normalized symbol/expiry pair."""
    return _expiry_types.get(
        (str(symbol or "").strip().upper(), _expiry_key(expiry)), "UNKNOWN"
    )

@router.get("/index-spots")
def index_spot_snapshot():
    """Return independent source-backed spot quotes for all supported Box Spread indices."""
    defaults = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX")
    if callable(_index_selection_provider):
        try:
            selected = set(_index_selection_provider())
            symbols = tuple(symbol for symbol in defaults if symbol in selected)
        except Exception:
            # Fail closed: never expose/fetch an unselected index on a setting error.
            symbols = ()
    else:
        symbols = defaults
    now_ns = time.time_ns()
    data = []
    for symbol in symbols:
        quote = _spot_fields(symbol)
        stamp = _timestamp_ns(quote.get("spot_timestamp_ns"))
        age = max(0.0, (now_ns - stamp) / 1_000_000_000) if stamp is not None and stamp <= now_ns else None
        price = quote.get("spot_price")
        fresh = price is not None and age is not None and age <= 5.0
        data.append({
            "symbol": symbol,
            "spot_price": price if fresh else None,
            "spot_timestamp_ns": stamp,
            "spot_age_seconds": round(age, 3) if age is not None else None,
            "status": "LIVE" if fresh else "STALE" if price is not None and age is not None else "NO_DATA",
        })
    return {"strategy": "box-spread", "freshness_target_seconds": 5, "data": data}


@router.get("/live")
def live(limit: Annotated[int, Query(ge=1,le=500)] = 50, max_age_seconds: Annotated[float, Query(gt=0,le=60)] = 5.0):
    rows=list(_latest()) if callable(_latest) else list(_latest)
    now_ns=time.time_ns()
    max_age_ns=int(max_age_seconds*1_000_000_000)
    cutoff=now_ns-max_age_ns
    max_skew_ns=min(max_age_ns,MAX_LEG_TIMESTAMP_SKEW_NS)
    fresh_rows=[]
    for row in rows:
        leg_timestamps = _leg_timestamps(row)
        # Reject malformed timestamps rather than silently falling back to strike time.
        if any(stamp is None for stamp in leg_timestamps):
            continue
        # All four executable option legs must be fresh and synchronized.
        if not all(cutoff <= stamp <= now_ns for stamp in leg_timestamps):
            continue
        if max(leg_timestamps) - min(leg_timestamps) > max_skew_ns:
            continue
        if getattr(row, "tradability_status", "UNVERIFIED") == "TRADABLE":
            fresh_rows.append(row)
    rows=fresh_rows[:limit]
    return {"strategy":"box-spread","mode":"paper-safe","count":len(rows),"opportunity_count":sum(float(r.executable_edge or 0)>0 for r in rows),"data":[{"symbol":r.low.underlying,"instrument_class":r.low.instrument_class,"expiry":r.low.expiry,"expiry_date":_expiry_date(r.low.expiry),"expiry_type":_expiry_types.get((str(r.low.underlying).strip().upper(), _expiry_key(r.low.expiry)), "UNKNOWN"),"low_strike":r.low.strike,"high_strike":r.high.strike,**_spot_fields(r.low.underlying),"direction":r.direction,"timestamp_ns":r.low.timestamp_ns,"high_timestamp_ns":r.high.timestamp_ns,"low_call_timestamp_ns":_leg_timestamps(r)[0],"low_put_timestamp_ns":_leg_timestamps(r)[1],"high_call_timestamp_ns":_leg_timestamps(r)[2],"high_put_timestamp_ns":_leg_timestamps(r)[3],"last_update_timestamp_ns":max(_leg_timestamps(r)),"quote_age_seconds":round(max(0.0,(now_ns-min(_leg_timestamps(r)))/1_000_000_000),3),"freshness":"LIVE","timestamp_skew_ms":round((max(int(getattr(r.low,"call_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.low,"put_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.high,"call_timestamp_ns",None) or r.high.timestamp_ns),int(getattr(r.high,"put_timestamp_ns",None) or r.high.timestamp_ns))-min(int(getattr(r.low,"call_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.low,"put_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.high,"call_timestamp_ns",None) or r.high.timestamp_ns),int(getattr(r.high,"put_timestamp_ns",None) or r.high.timestamp_ns)))/1_000_000,3),"low_call_ltp":getattr(r.low,"call_ltp",None),"low_call_bid":r.low.call_bid,"low_call_bid_qty":getattr(r.low,"call_bid_qty",None),"low_call_ask":r.low.call_ask,"low_call_ask_qty":getattr(r.low,"call_ask_qty",None),"low_put_ltp":getattr(r.low,"put_ltp",None),"low_put_bid":r.low.put_bid,"low_put_bid_qty":getattr(r.low,"put_bid_qty",None),"low_put_ask":r.low.put_ask,"low_put_ask_qty":getattr(r.low,"put_ask_qty",None),"high_call_ltp":getattr(r.high,"call_ltp",None),"high_call_bid":r.high.call_bid,"high_call_bid_qty":getattr(r.high,"call_bid_qty",None),"high_call_ask":r.high.call_ask,"high_call_ask_qty":getattr(r.high,"call_ask_qty",None),"high_put_ltp":getattr(r.high,"put_ltp",None),"high_put_bid":r.high.put_bid,"high_put_bid_qty":getattr(r.high,"put_bid_qty",None),"high_put_ask":r.high.put_ask,"high_put_ask_qty":getattr(r.high,"put_ask_qty",None),"tradability_status":getattr(r, "tradability_status", "UNVERIFIED"),"tradability_reason":getattr(r, "tradability_reason", None),"l1_reference_edge":getattr(r, "l1_reference_edge", None),"depth_edge_per_unit":getattr(r, "depth_edge_per_unit", None),"depth_edge_per_lot":getattr(r, "depth_edge_per_lot", None),"depth_executable_lots":getattr(r, "depth_executable_lots", 0),"depth_quantity":getattr(r, "depth_quantity", 0),"depth_legs":list(getattr(r, "depth_legs", ()) or ()),"depth_entry_prices":dict(getattr(r, "depth_entry_prices", {}) or {}),"depth_entry_type":getattr(r, "depth_entry_type", None),"depth_net_entry_price":getattr(r, "depth_net_entry_price", None),"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.low.lot_size,"max_executable_lots":getattr(r, "max_executable_lots", 0),"strike_distance":r.strike_distance,"liquidity_qty":min(r.low.volume,r.high.volume),"low_call_price":r.low.call_ask if r.direction=="LONG" else r.low.call_bid,"low_put_price":r.low.put_ask if r.direction=="LONG" else r.low.put_bid,"high_call_price":r.high.call_bid if r.direction=="LONG" else r.high.call_ask,"high_put_price":r.high.put_bid if r.direction=="LONG" else r.high.put_ask} for r in rows]}


def _serialize_monitor_result(result, *, now_ns: int, max_age_ns: int, freshness: str):
    from math import isfinite
    low, high = result.low, result.high
    stamps = _leg_timestamps(SimpleNamespace(low=low, high=high))
    if any(stamp is None for stamp in stamps):
        return None
    if not all(now_ns - max_age_ns <= stamp <= now_ns for stamp in stamps):
        return None
    if max(stamps) - min(stamps) > min(max_age_ns, MAX_LEG_TIMESTAMP_SKEW_NS):
        return None
    edge = float(result.executable_edge)
    if not isfinite(edge):
        return None
    return {
        "symbol": low.underlying, "instrument_class": low.instrument_class,
        "expiry": low.expiry, "expiry_date": _expiry_date(low.expiry),
        "expiry_type": classify_expiry_type(low.underlying, low.expiry),
        "low_strike": low.strike, "high_strike": high.strike,
        "direction": result.direction, **_spot_fields(low.underlying),
        "timestamp_ns": low.timestamp_ns, "high_timestamp_ns": high.timestamp_ns,
        "low_call_timestamp_ns": stamps[0], "low_put_timestamp_ns": stamps[1],
        "high_call_timestamp_ns": stamps[2], "high_put_timestamp_ns": stamps[3],
        "timestamp_skew_ms": round((max(stamps) - min(stamps)) / 1_000_000, 3),
        "last_update_timestamp_ns": max(stamps),
        "quote_age_seconds": round(max(0.0, (now_ns - min(stamps)) / 1_000_000_000), 3),
        "freshness": freshness,
        "low_call_bid": low.call_bid, "low_call_ask": low.call_ask,
        "low_put_bid": low.put_bid, "low_put_ask": low.put_ask,
        "high_call_bid": high.call_bid, "high_call_ask": high.call_ask,
        "high_put_bid": high.put_bid, "high_put_ask": high.put_ask,
        "low_call_bid_qty": getattr(low, "call_bid_qty", None),
        "low_call_ask_qty": getattr(low, "call_ask_qty", None),
        "low_put_bid_qty": getattr(low, "put_bid_qty", None),
        "low_put_ask_qty": getattr(low, "put_ask_qty", None),
        "high_call_bid_qty": getattr(high, "call_bid_qty", None),
        "high_call_ask_qty": getattr(high, "call_ask_qty", None),
        "high_put_bid_qty": getattr(high, "put_bid_qty", None),
        "high_put_ask_qty": getattr(high, "put_ask_qty", None),
        "executable_edge": edge, "edge_per_lot": result.edge_per_lot,
        "gross_pnl": result.gross_pnl, "lot_size": low.lot_size, "max_executable_lots": getattr(result, "max_executable_lots", 0),
        "low_call_price": low.call_ask if result.direction == "LONG" else low.call_bid,
        "low_put_price": low.put_ask if result.direction == "LONG" else low.put_bid,
        "high_call_price": high.call_bid if result.direction == "LONG" else high.call_ask,
        "high_put_price": high.put_bid if result.direction == "LONG" else high.put_ask,
    }


@router.get("/monitor")
def monitor_snapshot(max_age_seconds: Annotated[float, Query(gt=0,le=60)] = 5.0):
    """Return fresh raw position marks and entry candidates below global edge threshold."""
    snapshot = _monitor_getter() if callable(_monitor_getter) else None
    if not isinstance(snapshot, dict):
        return {"strategy": "box-spread", "fresh": False, "count": 0, "data": [], "entry_count": 0, "entry_data": []}
    now_ns = time.time_ns()
    updated_at_ns = int(snapshot.get("updated_at_ns") or 0)
    max_age_ns = int(max_age_seconds * 1_000_000_000)
    if updated_at_ns <= 0 or now_ns - updated_at_ns > max_age_ns or updated_at_ns > now_ns + 1_000_000_000:
        return {"strategy": "box-spread", "fresh": False, "count": 0, "data": [], "entry_count": 0, "entry_data": []}
    data = [
        item for result in snapshot.get("data", ())
        if (item := _serialize_monitor_result(result, now_ns=now_ns, max_age_ns=max_age_ns, freshness="LIVE_MONITOR")) is not None
    ]
    entry_data = [
        item for result in snapshot.get("entry_data", ())
        if (item := _serialize_monitor_result(result, now_ns=now_ns, max_age_ns=max_age_ns, freshness="LIVE_RULE_CANDIDATE")) is not None
    ]
    return {
        "strategy": "box-spread", "fresh": True,
        "count": len(data), "data": data,
        "entry_count": len(entry_data), "entry_data": entry_data,
    }

@router.get("/snapshots")
def snapshots(
    days: int = Query(1, ge=1, le=30),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    expiry_type: str | None = Query(None, pattern="^(WEEKLY|MONTHLY|UNKNOWN)$"),
    symbol: Annotated[str | None, Query(pattern="^(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|SENSEX|BANKEX)$")] = None,
):
    """Return only qualified opportunities; counts and pagination share one filter."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone
    from app.market_data.live_box_spread_runner import INDEX_EDGE_THRESHOLDS
    from app.core.config import settings

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    counts = Counter()
    page = []
    selected_count = 0

    def qualified(item, row_symbol):
        try:
            minimum_edge = float(INDEX_EDGE_THRESHOLDS.get(str(row_symbol).strip().upper(), 10.0))
            return (
                str(item.get("tradability_status") or "").strip().upper() == "TRADABLE"
                and int(item.get("max_executable_lots") or 0) >= max(2, int(settings.LIVE_BOX_SPREAD_MIN_EXECUTABLE_LOTS))
                and float(item.get("depth_edge_per_unit")) >= minimum_edge
            )
        except (TypeError, ValueError, OverflowError):
            return False

    with SessionLocal() as db:
        query = db.query(LiveBoxSpreadSnapshot).filter(
            LiveBoxSpreadSnapshot.observed_at >= cutoff
        )
        if symbol is not None:
            query = query.filter(LiveBoxSpreadSnapshot.symbol == symbol)
        rows = query.order_by(
            LiveBoxSpreadSnapshot.observed_at.desc(),
            LiveBoxSpreadSnapshot.id.desc(),
        ).yield_per(500)

        for row in rows:
            try:
                item = json.loads(row.payload_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict) or not qualified(item, row.symbol):
                continue

            kind = str(getattr(row, "expiry_type", None) or item.get("expiry_type") or "").strip().upper()
            if kind not in {"WEEKLY", "MONTHLY"}:
                current_kind = classify_expiry_type(row.symbol, row.expiry)
                kind = current_kind if current_kind in {"WEEKLY", "MONTHLY"} else "UNKNOWN"
            counts[kind] += 1
            if expiry_type is not None and kind != expiry_type:
                continue
            if offset <= selected_count < offset + limit:
                item["expiry_type"] = kind
                item["observed_at"] = row.observed_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                item["freshness"] = "SAVED SNAPSHOT"
                page.append(item)
            selected_count += 1

    total_count = selected_count
    if expiry_type is not None:
        # Keep the response's category totals global to the selected underlying,
        # while the requested view's total_count reflects its filtered expiry.
        pass
    return {
        "strategy": "box-spread", "days": days, "expiry_type": expiry_type,
        "symbol": symbol, "count": len(page), "total_count": total_count,
        "expiry_counts": {
            "WEEKLY": counts["WEEKLY"],
            "MONTHLY": counts["MONTHLY"],
            "UNKNOWN": counts["UNKNOWN"],
        },
        "offset": offset, "limit": limit,
        "has_more": offset + len(page) < total_count, "data": page,
    }


@router.get("/analysis")
def edge_timing_analysis():
    """Analyze every persisted Box Spread snapshot without sampling or changing scanner state."""
    from collections import defaultdict
    from datetime import timezone
    from zoneinfo import ZoneInfo
    from math import isfinite

    india = ZoneInfo("Asia/Kolkata")
    weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    def empty_group():
        return {"count": 0, "sum_edge": 0.0, "max_edge": 0.0, "sum_edge_per_lot": 0.0, "edge_per_lot_count": 0, "dates": set()}

    day_groups = defaultdict(empty_group)
    time_groups = defaultdict(empty_group)
    positive_edge_count = 0
    total_rows = 0
    latest_observed = None

    with SessionLocal() as db:
        # No date filter or row limit: analyze the complete retained snapshot history.
        # Stream rows and keep only compact bucket aggregates to avoid loading full history into memory.
        rows = db.query(
            LiveBoxSpreadSnapshot.observed_at,
            LiveBoxSpreadSnapshot.payload_json,
        ).order_by(
            LiveBoxSpreadSnapshot.observed_at.asc(),
            LiveBoxSpreadSnapshot.id.asc(),
        ).yield_per(1000)
        for observed, payload_json in rows:
            total_rows += 1
            if observed is None:
                continue
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            else:
                observed = observed.astimezone(timezone.utc)
            if latest_observed is None or observed > latest_observed:
                latest_observed = observed
            try:
                payload = json.loads(payload_json or "{}")
                edge = float(payload.get("executable_edge"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isfinite(edge) or edge <= 0:
                continue

            local = observed.astimezone(india)
            day = weekdays[local.weekday()]
            minute_bucket = (local.hour * 60 + local.minute) // 30 * 30
            time_bucket = f"{minute_bucket // 60:02d}:{minute_bucket % 60:02d}"
            edge_per_lot = _finite_number(payload.get("edge_per_lot"))
            for group in (day_groups[day], time_groups[time_bucket]):
                group["count"] += 1
                group["sum_edge"] += edge
                group["max_edge"] = max(group["max_edge"], edge)
                group["dates"].add(local.date().isoformat())
                if edge_per_lot is not None:
                    group["sum_edge_per_lot"] += edge_per_lot
                    group["edge_per_lot_count"] += 1
            positive_edge_count += 1

    def stats(key, group):
        return {
            "key": key,
            "count": group["count"],
            "distinct_dates": len(group["dates"]),
            "max_edge": round(group["max_edge"], 4),
            "average_edge": round(group["sum_edge"] / group["count"], 4),
            "average_edge_per_lot": round(
                group["sum_edge_per_lot"] / group["edge_per_lot_count"], 2
            ) if group["edge_per_lot_count"] else None,
        }

    day_stats = [stats(day, values) for day, values in day_groups.items()]
    time_stats = [stats(bucket, values) for bucket, values in time_groups.items()]
    day_stats.sort(key=lambda item: weekdays.index(item["key"]))
    time_stats.sort(key=lambda item: item["key"])
    by_max_day = max(day_stats, key=lambda item: item["max_edge"], default=None)
    by_max_time = max(time_stats, key=lambda item: item["max_edge"], default=None)
    reliable_days = [item for item in day_stats if item["distinct_dates"] >= 5]
    reliable_times = [item for item in time_stats if item["distinct_dates"] >= 5]
    by_average_day = max(reliable_days, key=lambda item: item["average_edge"], default=None)
    by_average_time = max(reliable_times, key=lambda item: item["average_edge"], default=None)

    if not positive_edge_count:
        conclusion = "No saved positive-edge observations are available yet. Keep collecting complete live snapshots before drawing a timing conclusion."
    elif by_average_time and by_average_day:
        conclusion = (
            f"Across {positive_edge_count} saved positive-edge observations, the strongest repeatable 30-minute window is "
            f"{by_average_time['key']} IST (average edge {by_average_time['average_edge']}, n={by_average_time['count']}); "
            f"{by_average_day['key']} has the highest average day edge ({by_average_day['average_edge']}, n={by_average_day['count']}). "
            "These are historical observations, not a guarantee of future edge."
        )
    else:
        conclusion = (
            f"{positive_edge_count} saved positive-edge observations are available. The largest single observed edge was "
            f"{by_max_time['key'] + ' IST' if by_max_time else 'not available'} and "
            f"{by_max_day['key'] if by_max_day else 'no day'}; there are not yet at least 5 distinct trading dates in both a weekday and a 30-minute window "
            "to call an average timing pattern reliable."
        )

    return {
        "strategy": "box-spread",
        "source": "all_saved_snapshots",
        "timezone": "Asia/Kolkata",
        "total_saved_snapshots": total_rows,
        "positive_edge_observations": positive_edge_count,
        "latest_observed_at": latest_observed.isoformat() if latest_observed else None,
        "minimum_samples_for_average_conclusion": 5,
        "minimum_distinct_dates_for_average_conclusion": 5,
        "best_max_edge_day": by_max_day,
        "best_max_edge_time": by_max_time,
        "best_average_edge_day": by_average_day,
        "best_average_edge_time": by_average_time,
        "by_day": day_stats,
        "by_time": time_stats,
        "conclusion": conclusion,
    }


def _finite_number(value):
    from math import isfinite
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


@router.get("/alerts")
def alerts(days:int=Query(30,ge=1,le=30),limit:int=Query(500,ge=1,le=5000)):
    """Return only alerts carrying the same persisted qualification proof as live rows."""
    from datetime import datetime,timedelta
    from app.market_data.live_box_spread_runner import INDEX_EDGE_THRESHOLDS
    from app.core.config import settings
    cutoff=datetime.utcnow()-timedelta(days=days)
    minimum_lots=max(2,int(settings.LIVE_BOX_SPREAD_MIN_EXECUTABLE_LOTS))
    with SessionLocal() as db:
        query=db.query(LiveBoxSpreadAlertHistory).filter(
            LiveBoxSpreadAlertHistory.observed_at>=cutoff,
            LiveBoxSpreadAlertHistory.tradability_status=="TRADABLE",
            LiveBoxSpreadAlertHistory.max_executable_lots>=minimum_lots,
        )
        rows=query.order_by(LiveBoxSpreadAlertHistory.observed_at.desc()).all()
        data=[]
        for r in rows:
            try:
                edge=float(getattr(r,"depth_edge_per_unit",None))
                lots=int(getattr(r,"max_executable_lots",None))
                status=str(getattr(r,"tradability_status",None) or "").strip().upper()
                threshold=float(INDEX_EDGE_THRESHOLDS.get(str(r.symbol).strip().upper(),10.0))
            except (TypeError,ValueError,OverflowError):
                continue
            if status != "TRADABLE" or lots < minimum_lots or not edge >= threshold:
                continue
            data.append({"symbol":r.symbol,"instrument_class":r.instrument_class,"expiry":r.expiry,"expiry_date":_expiry_date(r.expiry),"expiry_type":(str(getattr(r,"expiry_type",None) or "").strip().upper() if str(getattr(r,"expiry_type",None) or "").strip().upper() in {"WEEKLY","MONTHLY"} else classify_expiry_type(r.symbol,r.expiry)),"low_strike":r.low_strike,"high_strike":r.high_strike,"direction":r.direction,"timestamp_ns":r.timestamp_ns,"low_call_bid":r.low_call_bid,"low_call_bid_qty":getattr(r,"low_call_bid_qty",None),"low_call_ask":r.low_call_ask,"low_call_ask_qty":getattr(r,"low_call_ask_qty",None),"low_put_bid":r.low_put_bid,"low_put_bid_qty":getattr(r,"low_put_bid_qty",None),"low_put_ask":r.low_put_ask,"low_put_ask_qty":getattr(r,"low_put_ask_qty",None),"high_call_bid":r.high_call_bid,"high_call_bid_qty":getattr(r,"high_call_bid_qty",None),"high_call_ask":r.high_call_ask,"high_call_ask_qty":getattr(r,"high_call_ask_qty",None),"high_put_bid":r.high_put_bid,"high_put_bid_qty":getattr(r,"high_put_bid_qty",None),"high_put_ask":r.high_put_ask,"high_put_ask_qty":getattr(r,"high_put_ask_qty",None),"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.lot_size,"depth_edge_per_unit":edge,"max_executable_lots":r.max_executable_lots,"tradability_status":r.tradability_status})
            if len(data)>=limit:
                break
        return {"strategy":"box-spread","days":days,"count":len(data),"data":data}


__all__=["router","configure","configure_monitor"]
