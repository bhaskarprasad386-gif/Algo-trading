"""HTTP access to live Box Spread opportunities and durable alerts."""
from typing import Annotated
import json
from fastapi import APIRouter, Query
import time
from app.models import LiveBoxSpreadAlertHistory, LiveBoxSpreadSnapshot
from app.core.database import SessionLocal

router=APIRouter(prefix="/api/v1/scanner/box-spread",tags=["box-spread"])
_latest=()
_diagnostics_getter = None
_spot_provider = None
_expiry_types = {}
MAX_LEG_TIMESTAMP_SKEW_NS = 3_000_000_000

def configure_spot_provider(provider):
    """Configure a source-backed, freshness-checked underlying spot getter."""
    global _spot_provider
    _spot_provider = provider

def _spot_fields(symbol):
    snapshot = _spot_provider(symbol) if callable(_spot_provider) else None
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

def _leg_timestamps(row):
    """Return timestamps for each executable option quote, with strike fallback."""
    low_ts = int(getattr(row.low, "timestamp_ns", 0) or 0)
    high_ts = int(getattr(row.high, "timestamp_ns", 0) or 0)
    return (
        int(getattr(row.low, "call_timestamp_ns", None) or low_ts),
        int(getattr(row.low, "put_timestamp_ns", None) or low_ts),
        int(getattr(row.high, "call_timestamp_ns", None) or high_ts),
        int(getattr(row.high, "put_timestamp_ns", None) or high_ts),
    )


def configure(getter):
    global _latest; _latest=getter

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
        # All four executable option legs must be fresh and synchronized.
        if not all(cutoff <= stamp <= now_ns for stamp in leg_timestamps):
            continue
        if max(leg_timestamps) - min(leg_timestamps) > max_skew_ns:
            continue
        fresh_rows.append(row)
    rows=fresh_rows[:limit]
    return {"strategy":"box-spread","mode":"paper-safe","count":len(rows),"opportunity_count":sum(float(r.executable_edge or 0)>0 for r in rows),"data":[{"symbol":r.low.underlying,"instrument_class":r.low.instrument_class,"expiry":r.low.expiry,"expiry_date":_expiry_date(r.low.expiry),"expiry_type":_expiry_types.get((str(r.low.underlying).strip().upper(), _expiry_key(r.low.expiry)), "UNKNOWN"),"low_strike":r.low.strike,"high_strike":r.high.strike,**_spot_fields(r.low.underlying),"direction":r.direction,"timestamp_ns":r.low.timestamp_ns,"high_timestamp_ns":r.high.timestamp_ns,"last_update_timestamp_ns":max(_leg_timestamps(r)),"quote_age_seconds":round(max(0.0,(now_ns-min(_leg_timestamps(r)))/1_000_000_000),3),"freshness":"LIVE","timestamp_skew_ms":round((max(int(getattr(r.low,"call_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.low,"put_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.high,"call_timestamp_ns",None) or r.high.timestamp_ns),int(getattr(r.high,"put_timestamp_ns",None) or r.high.timestamp_ns))-min(int(getattr(r.low,"call_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.low,"put_timestamp_ns",None) or r.low.timestamp_ns),int(getattr(r.high,"call_timestamp_ns",None) or r.high.timestamp_ns),int(getattr(r.high,"put_timestamp_ns",None) or r.high.timestamp_ns)))/1_000_000,3),"low_call_ltp":getattr(r.low,"call_ltp",None),"low_call_bid":r.low.call_bid,"low_call_bid_qty":getattr(r.low,"call_bid_qty",None),"low_call_ask":r.low.call_ask,"low_call_ask_qty":getattr(r.low,"call_ask_qty",None),"low_put_ltp":getattr(r.low,"put_ltp",None),"low_put_bid":r.low.put_bid,"low_put_bid_qty":getattr(r.low,"put_bid_qty",None),"low_put_ask":r.low.put_ask,"low_put_ask_qty":getattr(r.low,"put_ask_qty",None),"high_call_ltp":getattr(r.high,"call_ltp",None),"high_call_bid":r.high.call_bid,"high_call_bid_qty":getattr(r.high,"call_bid_qty",None),"high_call_ask":r.high.call_ask,"high_call_ask_qty":getattr(r.high,"call_ask_qty",None),"high_put_ltp":getattr(r.high,"put_ltp",None),"high_put_bid":r.high.put_bid,"high_put_bid_qty":getattr(r.high,"put_bid_qty",None),"high_put_ask":r.high.put_ask,"high_put_ask_qty":getattr(r.high,"put_ask_qty",None),"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.low.lot_size,"strike_distance":r.strike_distance,"liquidity_qty":min(r.low.volume,r.high.volume),"low_call_price":r.low.call_ask if r.direction=="LONG" else r.low.call_bid,"low_put_price":r.low.put_ask if r.direction=="LONG" else r.low.put_bid,"high_call_price":r.high.call_bid if r.direction=="LONG" else r.high.call_ask,"high_put_price":r.high.put_bid if r.direction=="LONG" else r.high.put_ask} for r in rows]}

@router.get("/snapshots")
def snapshots(
    days: int = Query(1, ge=1, le=30),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    expiry_type: str | None = Query(None, pattern="^(WEEKLY|MONTHLY|UNKNOWN)$"),
):
    """Return expiry-filtered persisted observations with counts for every class."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with SessionLocal() as db:
        rows = (
            db.query(LiveBoxSpreadSnapshot)
            .filter(LiveBoxSpreadSnapshot.observed_at >= cutoff)
            .order_by(
                LiveBoxSpreadSnapshot.observed_at.desc(),
                LiveBoxSpreadSnapshot.id.desc(),
            )
            .all()
        )
        classified = []
        expiry_counts = Counter()
        for row in rows:
            try:
                item = json.loads(row.payload_json)
            except (TypeError, ValueError):
                continue
            saved_type = str(item.get("expiry_type") or "").strip().upper()
            current_type = classify_expiry_type(row.symbol, row.expiry)
            kind = (
                saved_type if saved_type in {"WEEKLY", "MONTHLY"}
                else current_type if current_type in {"WEEKLY", "MONTHLY"}
                else "UNKNOWN"
            )
            expiry_counts[kind] += 1
            item["expiry_type"] = kind
            item["observed_at"] = row.observed_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            item["freshness"] = "SAVED SNAPSHOT"
            classified.append(item)

        selected = [
            item for item in classified
            if expiry_type is None or item["expiry_type"] == expiry_type
        ]
        total_count = len(selected)
        page = selected[offset:offset + limit]
        return {
            "strategy": "box-spread",
            "days": days,
            "expiry_type": expiry_type,
            "count": len(page),
            "total_count": total_count,
            "expiry_counts": {
                "WEEKLY": expiry_counts["WEEKLY"],
                "MONTHLY": expiry_counts["MONTHLY"],
                "UNKNOWN": expiry_counts["UNKNOWN"],
            },
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < total_count,
            "data": page,
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
        rows = db.query(LiveBoxSpreadSnapshot).order_by(
            LiveBoxSpreadSnapshot.observed_at.asc(), LiveBoxSpreadSnapshot.id.asc()
        ).yield_per(1000)
        for row in rows:
            total_rows += 1
            observed = row.observed_at
            if observed is None:
                continue
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            else:
                observed = observed.astimezone(timezone.utc)
            if latest_observed is None or observed > latest_observed:
                latest_observed = observed
            try:
                payload = json.loads(row.payload_json or "{}")
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
    from datetime import datetime,timedelta
    cutoff=datetime.utcnow()-timedelta(days=days)
    with SessionLocal() as db:
        rows=db.query(LiveBoxSpreadAlertHistory).filter(LiveBoxSpreadAlertHistory.observed_at>=cutoff).order_by(LiveBoxSpreadAlertHistory.observed_at.desc()).limit(limit).all()
        return {"strategy":"box-spread","days":days,"count":len(rows),"data":[{"symbol":r.symbol,"instrument_class":r.instrument_class,"expiry":r.expiry,"expiry_type":(str(getattr(r,"expiry_type",None) or "").strip().upper() if str(getattr(r,"expiry_type",None) or "").strip().upper() in {"WEEKLY","MONTHLY"} else classify_expiry_type(r.symbol,r.expiry)),"low_strike":r.low_strike,"high_strike":r.high_strike,"direction":r.direction,"timestamp_ns":r.timestamp_ns,"low_call_bid":r.low_call_bid,"low_call_bid_qty":getattr(r,"low_call_bid_qty",None),"low_call_ask":r.low_call_ask,"low_call_ask_qty":getattr(r,"low_call_ask_qty",None),"low_put_bid":r.low_put_bid,"low_put_bid_qty":getattr(r,"low_put_bid_qty",None),"low_put_ask":r.low_put_ask,"low_put_ask_qty":getattr(r,"low_put_ask_qty",None),"high_call_bid":r.high_call_bid,"high_call_bid_qty":getattr(r,"high_call_bid_qty",None),"high_call_ask":r.high_call_ask,"high_call_ask_qty":getattr(r,"high_call_ask_qty",None),"high_put_bid":r.high_put_bid,"high_put_bid_qty":getattr(r,"high_put_bid_qty",None),"high_put_ask":r.high_put_ask,"high_put_ask_qty":getattr(r,"high_put_ask_qty",None),"executable_edge":r.executable_edge,"edge_per_lot":r.edge_per_lot,"gross_pnl":r.gross_pnl,"lot_size":r.lot_size} for r in rows]}

__all__=["router","configure"]
