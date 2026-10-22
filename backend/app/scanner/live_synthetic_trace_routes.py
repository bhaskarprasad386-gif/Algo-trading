"""Explicit, on-demand exact-contract status checks and durable trace history."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
from math import isfinite
from builtins import max
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/scanner/synthetic-cash-carry/trace", tags=["Synthetic Trace"])

_ALLOWED_INDEXES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
_EXCHANGE_BY_INDEX = {"SENSEX": "BFO", "BANKEX": "BFO"}


class TraceOpportunityRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=128)
    trace_key: str = Field(min_length=1, max_length=512)
    traced_at: str = Field(min_length=10, max_length=64)
    snapshot: dict[str, object]


class TraceStatusRequest(BaseModel):
    trace_id: str = Field(default="untracked", min_length=1, max_length=128)
    underlying: str = Field(min_length=1, max_length=32)
    expiry: str = Field(min_length=8, max_length=32)
    strike: float = Field(gt=0, le=10_000_000)
    direction: Literal["LONG", "SHORT"]
    entry_edge: float
    lot_size: int | None = Field(default=None, ge=1, le=10_000_000)
    entry_future_bid: float | None = None
    entry_future_ask: float | None = None
    entry_call_bid: float | None = None
    entry_call_ask: float | None = None
    entry_put_bid: float | None = None
    entry_put_ask: float | None = None


def _expiry_date(raw: object):
    text = str(raw or "").strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d%b%Y", "%d%b%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _positive_price(raw):
    if raw is None or isinstance(raw, bool):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number > 0 else None


def _depth_side(quote: dict, side: str):
    depth = quote.get("depth")
    if isinstance(depth, dict):
        levels = depth.get("buy" if side == "bid" else "sell")
        if isinstance(levels, list) and levels and isinstance(levels[0], dict):
            price = _positive_price(levels[0].get("price"))
            if price is not None:
                return price
    legacy = quote.get("best_5_buy_data" if side == "bid" else "best_5_sell_data")
    if isinstance(legacy, list) and legacy and isinstance(legacy[0], dict):
        return _positive_price(legacy[0].get("price"))
    return None


def _resolve_contracts(master, symbol: str, expiry_text: str, strike: float):
    from app.market_data.synthetic_subscriptions import _strike as _master_strike

    segment = _EXCHANGE_BY_INDEX.get(symbol, "NFO")
    target_expiry = _expiry_date(expiry_text)
    if target_expiry is None:
        raise ValueError("Trace expiry is not a recognized date")
    rows = [
        item for item in master.instruments
        if str(item.get("exch_seg", "")).strip().upper() == segment
        and str(item.get("name", "")).strip().upper() == symbol
        and _expiry_date(item.get("expiry")) == target_expiry
    ]
    futures = [item for item in rows if str(item.get("instrumenttype", "")).strip().upper() == "FUTIDX"]
    options = [item for item in rows if str(item.get("instrumenttype", "")).strip().upper() == "OPTIDX"]
    calls = [item for item in options if str(item.get("symbol", "")).strip().upper().endswith("CE")
             and abs(_master_strike(item.get("strike")) - strike) < 1e-6]
    puts = [item for item in options if str(item.get("symbol", "")).strip().upper().endswith("PE")
            and abs(_master_strike(item.get("strike")) - strike) < 1e-6]
    resolved = {}
    for role, candidates in (("future", futures), ("call", calls), ("put", puts)):
        candidates = [item for item in candidates if str(item.get("token", "")).strip() and str(item.get("symbol", "")).strip()]
        if len(candidates) != 1:
            reason = "not found" if not candidates else "ambiguous in instrument master"
            raise LookupError(f"Exact {role} contract {reason} for {symbol} {target_expiry} strike {strike:g}")
        resolved[role] = candidates[0]
    return segment, resolved


_TRACE_MAX_QUOTE_AGE_SECONDS = 10.0
_TRACE_CLOCK_SKEW_SECONDS = 5.0


def _quote_timestamp(quote: dict):
    for field in ("exchFeedTime", "exchTradeTime", "exchange_timestamp", "timestamp"):
        raw = quote.get(field)
        if raw is None or str(raw).strip() == "":
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            try:
                stamp = float(raw)
                if stamp > 10_000_000_000:
                    stamp /= 1000.0
                return datetime.fromtimestamp(stamp, tz=ZoneInfo("Asia/Kolkata"))
            except (ValueError, OverflowError, OSError):
                continue
        text = str(raw).strip()
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            stamp = None
            for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    stamp = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if stamp is not None:
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            return stamp.astimezone(ZoneInfo("Asia/Kolkata"))
    return None


def _validate_quote_freshness(quote: dict, role: str):
    stamp = _quote_timestamp(quote)
    if stamp is None:
        raise LookupError(f"Exact {role} quote has no exchange timestamp; refusing to report a possibly stale edge")
    age = (datetime.now(ZoneInfo("Asia/Kolkata")) - stamp).total_seconds()
    if age > _TRACE_MAX_QUOTE_AGE_SECONDS:
        raise LookupError(f"Exact {role} quote is stale ({age:.1f}s old; limit {_TRACE_MAX_QUOTE_AGE_SECONDS:g}s)")
    if age < -_TRACE_CLOCK_SKEW_SECONDS:
        raise LookupError(f"Exact {role} quote timestamp is in the future ({-age:.1f}s); refusing the edge")
    return max(0.0, age)


def _quote_rows(response):
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict):
        fetched = data.get("fetched")
        if isinstance(fetched, list):
            return fetched
    if isinstance(data, list):
        return data
    return []


def _calculate_close_pnl(request: TraceStatusRequest, future: dict, synthetic_bid: float, synthetic_ask: float):
    entry = {
        "future_bid": _positive_price(request.entry_future_bid),
        "future_ask": _positive_price(request.entry_future_ask),
        "call_bid": _positive_price(request.entry_call_bid),
        "call_ask": _positive_price(request.entry_call_ask),
        "put_bid": _positive_price(request.entry_put_bid),
        "put_ask": _positive_price(request.entry_put_ask),
    }
    if any(value is None for value in entry.values()) or not request.lot_size:
        return {
            "pnl_status": "UNAVAILABLE",
            "pnl_detail": "Entry bid/ask snapshot or lot size was not saved; close P&L cannot be reconstructed reliably.",
            "pnl_per_unit": None, "pnl_per_lot": None,
            "entry_synthetic_bid": None, "entry_synthetic_ask": None,
        }
    entry_synthetic_bid = request.strike + entry["call_bid"] - entry["put_ask"]
    entry_synthetic_ask = request.strike + entry["call_ask"] - entry["put_bid"]
    if request.direction == "LONG":
        # LONG = buy future at entry ask and sell synthetic at entry bid.
        pnl_per_unit = (future["bid"] - entry["future_ask"]) + (entry_synthetic_bid - synthetic_ask)
    else:
        # SHORT = sell future at entry bid and buy synthetic at entry ask.
        pnl_per_unit = (entry["future_bid"] - future["ask"]) + (synthetic_bid - entry_synthetic_ask)
    return {
        "pnl_status": "GROSS_ESTIMATE",
        "pnl_detail": "Executable bid/ask estimate before brokerage, taxes, slippage and other charges.",
        "pnl_per_unit": pnl_per_unit,
        "pnl_per_lot": pnl_per_unit * request.lot_size,
        "entry_synthetic_bid": entry_synthetic_bid,
        "entry_synthetic_ask": entry_synthetic_ask,
    }


def _save_check(request, payload):
    from app.core.database import SessionLocal
    from app.models.synthetic_trace_status_check import SyntheticTraceStatusCheck

    checked = datetime.fromisoformat(payload["checked_at"].replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    data = payload
    record = SyntheticTraceStatusCheck(
        trace_id=request.trace_id,
        checked_at=checked,
        underlying=request.underlying.strip().upper(),
        expiry=str(payload.get("expiry") or request.expiry),
        strike=request.strike,
        direction=request.direction,
        entry_edge=payload.get("entry_edge"),
        current_edge=payload.get("current_edge"),
        edge_change=payload.get("edge_change"),
        future_bid=(payload.get("contracts") or {}).get("future", {}).get("bid"),
        future_ask=(payload.get("contracts") or {}).get("future", {}).get("ask"),
        call_bid=(payload.get("contracts") or {}).get("call", {}).get("bid"),
        call_ask=(payload.get("contracts") or {}).get("call", {}).get("ask"),
        put_bid=(payload.get("contracts") or {}).get("put", {}).get("bid"),
        put_ask=(payload.get("contracts") or {}).get("put", {}).get("ask"),
        synthetic_bid=payload.get("synthetic_bid"),
        synthetic_ask=payload.get("synthetic_ask"),
        lot_size=request.lot_size,
        pnl_per_unit=payload.get("pnl_per_unit"),
        pnl_per_lot=payload.get("pnl_per_lot"),
        pnl_status=payload.get("pnl_status", "UNAVAILABLE"),
        detail=payload.get("detail") or payload.get("pnl_detail"),
    )
    db = SessionLocal()
    try:
        db.add(record)
        db.commit()
        db.refresh(record)
        return record.id
    finally:
        db.close()


def _save_failed_check(request: TraceStatusRequest, detail: str):
    """Persist a failed explicit check too; failures must not disappear from the audit trail."""
    try:
        from app.core.database import SessionLocal
        from app.models.synthetic_trace_status_check import SyntheticTraceStatusCheck
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db = SessionLocal()
        try:
            db.add(SyntheticTraceStatusCheck(
                trace_id=request.trace_id,
                checked_at=now,
                underlying=request.underlying.strip().upper(),
                expiry=str(request.expiry),
                strike=request.strike,
                direction=request.direction,
                entry_edge=request.entry_edge,
                current_edge=None,
                edge_change=None,
                future_bid=None, future_ask=None,
                call_bid=None, call_ask=None, put_bid=None, put_ask=None,
                synthetic_bid=None, synthetic_ask=None,
                lot_size=request.lot_size,
                pnl_per_unit=None, pnl_per_lot=None,
                pnl_status="UNAVAILABLE",
                detail=detail[:2000],
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        # A history-storage outage must not mask the real quote-resolution error.
        pass


def _history_rows(trace_id: str, limit: int = 20):
    from app.core.database import SessionLocal
    from app.models.synthetic_trace_status_check import SyntheticTraceStatusCheck

    db = SessionLocal()
    try:
        rows = (db.query(SyntheticTraceStatusCheck)
                .filter(SyntheticTraceStatusCheck.trace_id == trace_id)
                .order_by(SyntheticTraceStatusCheck.checked_at.desc(), SyntheticTraceStatusCheck.id.desc())
                .limit(limit).all())
        return [{
            "id": row.id,
            "trace_id": row.trace_id,
            "checked_at": row.checked_at.replace(tzinfo=timezone.utc).isoformat(),
            "underlying": row.underlying,
            "expiry": row.expiry,
            "strike": row.strike,
            "direction": row.direction,
            "entry_edge": row.entry_edge,
            "current_edge": row.current_edge,
            "edge_change": row.edge_change,
            "pnl_status": row.pnl_status,
            "pnl_per_unit": row.pnl_per_unit,
            "pnl_per_lot": row.pnl_per_lot,
            "detail": row.detail,
        } for row in rows]
    finally:
        db.close()


def _trace_snapshot_record(trace_id: str, trace_key: str, traced_at: str, snapshot: dict):
    from app.core.database import SessionLocal
    from app.models.synthetic_trace_opportunity import SyntheticTraceOpportunity

    try:
        encoded = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
        traced = datetime.fromisoformat(traced_at.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
        underlying = str(snapshot.get("underlying") or "").strip().upper()
        direction = str(snapshot.get("direction") or "").strip().upper()
        expiry = str(snapshot.get("expiry") or "").strip()
        strike = float(snapshot.get("strike"))
        edge = float(snapshot.get("executable_edge", snapshot.get("long_edge" if direction == "LONG" else "short_edge")))
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail="Trace snapshot contains invalid identity, time, strike or edge") from exc
    if len(encoded) > 100_000:
        raise HTTPException(status_code=413, detail="Trace snapshot exceeds the 100 KB limit")
    if underlying not in _ALLOWED_INDEXES or direction not in {"LONG", "SHORT"} or not expiry or not isfinite(strike) or strike <= 0 or not isfinite(edge):
        raise HTTPException(status_code=422, detail="Trace snapshot must identify a supported index, expiry, strike, direction and finite entry edge")
    if str(snapshot.get("trace_id") or trace_id) != trace_id:
        raise HTTPException(status_code=422, detail="Trace ID does not match the snapshot")
    db = SessionLocal()
    try:
        row = db.get(SyntheticTraceOpportunity, trace_id)
        if row is None:
            row = SyntheticTraceOpportunity(
                trace_id=trace_id, trace_key=trace_key, traced_at=traced,
                underlying=underlying, expiry=expiry, strike=strike,
                direction=direction, entry_edge=edge, snapshot_json=encoded,
            )
            db.add(row)
        else:
            row.trace_key = trace_key
            row.traced_at = traced
            row.underlying = underlying
            row.expiry = expiry
            row.strike = strike
            row.direction = direction
            row.entry_edge = edge
            row.snapshot_json = encoded
        db.commit()
        return {"trace_id": trace_id, "persisted": True}
    finally:
        db.close()


@router.post("/opportunities")
def save_trace_opportunity(request: TraceOpportunityRequest):
    return _trace_snapshot_record(request.trace_id, request.trace_key, request.traced_at, request.snapshot)


@router.get("/opportunities")
def list_trace_opportunities(limit: int = Query(default=200, ge=1, le=500)):
    from app.core.database import SessionLocal
    from app.models.synthetic_trace_opportunity import SyntheticTraceOpportunity

    db = SessionLocal()
    try:
        rows = (db.query(SyntheticTraceOpportunity)
                .order_by(SyntheticTraceOpportunity.traced_at.desc())
                .limit(limit).all())
        return {"count": len(rows), "data": [
            {**json.loads(row.snapshot_json),
             "trace_id": row.trace_id,
             "trace_key": row.trace_key,
             "traced_at": row.traced_at.replace(tzinfo=timezone.utc).isoformat(),
             "persisted": True}
            for row in rows
        ]}
    finally:
        db.close()


@router.delete("/opportunities/{trace_id}")
def delete_trace_opportunity(trace_id: str):
    from app.core.database import SessionLocal
    from app.models.synthetic_trace_opportunity import SyntheticTraceOpportunity

    db = SessionLocal()
    try:
        row = db.get(SyntheticTraceOpportunity, trace_id)
        if row is not None:
            db.delete(row)
            db.commit()
        return {"trace_id": trace_id, "deleted": True}
    finally:
        db.close()


@router.post("/live-status")
def check_trace_live_status(request: TraceStatusRequest):
    """Fetch exact traced-contract depth once, only when the user clicks Check."""
    symbol = request.underlying.strip().upper()
    if symbol not in _ALLOWED_INDEXES:
        raise HTTPException(status_code=422, detail="Trace status supports the six configured indices only")
    if not isfinite(float(request.entry_edge)):
        raise HTTPException(status_code=422, detail="Entry edge must be finite")
    try:
        from app.market_data.instruments import InstrumentMaster
        from app.market_data.client import MarketDataClient

        master = InstrumentMaster()
        master.download()
        segment, contracts = _resolve_contracts(master, symbol, request.expiry, float(request.strike))
        response = MarketDataClient().quote_many(segment, [
            {"tradingsymbol": str(contracts[role]["symbol"]), "symboltoken": str(contracts[role]["token"])}
            for role in ("future", "call", "put")
        ])
        quote_by_token = {str(item.get("symbolToken", item.get("symboltoken", ""))).strip(): item
                          for item in _quote_rows(response) if isinstance(item, dict)}
        legs = {}
        for role, instrument in contracts.items():
            quote = quote_by_token.get(str(instrument["token"]).strip())
            if quote is None:
                raise LookupError(f"Live quote missing for exact {role} contract {instrument['symbol']}")
            quote_age = _validate_quote_freshness(quote, role)
            legs[role] = {"symbol": str(instrument["symbol"]), "bid": _depth_side(quote, "bid"),
                          "ask": _depth_side(quote, "ask"), "ltp": _positive_price(quote.get("ltp")),
                          "quote_age_seconds": round(quote_age, 3)}
        future, call, put = legs["future"], legs["call"], legs["put"]
        if any(legs[role][side] is None for role in legs for side in ("bid", "ask")):
            raise LookupError("One or more exact contracts have no valid bid/ask depth; executable edge is unavailable")
        synthetic_bid = float(request.strike) + call["bid"] - put["ask"]
        synthetic_ask = float(request.strike) + call["ask"] - put["bid"]
        # Keep direction definitions aligned with the scanner's approved executable formulas.
        long_edge = synthetic_bid - future["ask"]
        short_edge = future["bid"] - synthetic_ask
        current_edge = long_edge if request.direction == "LONG" else short_edge
        pnl = _calculate_close_pnl(request, future, synthetic_bid, synthetic_ask)
        payload = {
            "status": "LIVE_QUOTES", "trace_id": request.trace_id,
            "underlying": symbol, "expiry": _expiry_date(request.expiry).isoformat(),
            "strike": float(request.strike), "direction": request.direction,
            "checked_at": datetime.now().astimezone().isoformat(), "exchange": segment,
            "contracts": legs, "synthetic_bid": synthetic_bid, "synthetic_ask": synthetic_ask,
            "max_quote_age_seconds": max(legs[role]["quote_age_seconds"] for role in legs),
            "long_edge": long_edge, "short_edge": short_edge, "entry_edge": float(request.entry_edge),
            "current_edge": current_edge, "edge_change": current_edge - float(request.entry_edge),
            **pnl, "quote_source": "Angel One FULL quote depth", "broker_orders": "OFF",
        }
        try:
            payload["history_record_id"] = _save_check(request, payload)
            payload["history_persistence"] = "SAVED"
            payload["history"] = _history_rows(request.trace_id)
        except Exception:
            # A database outage must not erase a valid live quote response.
            payload["history_record_id"] = None
            payload["history_persistence"] = "UNAVAILABLE"
            payload["history_detail"] = "Live quotes are available, but this check could not be saved to history."
            payload["history"] = []
        return payload
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        _save_failed_check(request, str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        detail = "Exact live quote request failed: " + type(exc).__name__
        _save_failed_check(request, detail)
        raise HTTPException(status_code=502, detail=detail) from exc


@router.get("/history")
def get_trace_status_history(trace_id: str = Query(min_length=1, max_length=128), limit: int = Query(default=20, ge=1, le=100)):
    rows = _history_rows(trace_id, limit)
    return {"trace_id": trace_id, "count": len(rows), "data": rows}
