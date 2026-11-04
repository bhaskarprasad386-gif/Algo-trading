"""Read-only Box Spread trade trace and live exit valuation endpoints.

This module never places, modifies, or cancels broker orders.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from threading import Lock
from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field
from app.core.database import SessionLocal
from app.models.box_spread_trace import BoxSpreadTrace, BoxSpreadTraceExitCheck
from app.market_data.instruments import InstrumentMaster
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.common_strategy_feed import shared_common_manager

router = APIRouter(prefix="/api/v1/scanner/box-spread/trace", tags=["box-spread-trace"])
_master = InstrumentMaster()
_lock = Lock()
_latest: dict[str, object] = {}
_feed: CommonStrategyMarketFeed | None = None
_feed_keys: set[str] = set()
_leg_names = ("low_call", "low_put", "high_call", "high_put")


class TraceCreate(BaseModel):
    snapshot: dict
    quantity_lots: int | None = Field(default=None, ge=1)


def _expiry_key(value):
    text = str(value or "").strip().upper()
    for fmt in ("%Y%m%d", "%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def _trace_expired(expiry):
    parsed = _expiry_key(expiry)
    try:
        expiry_date = datetime.fromisoformat(parsed).date()
    except (TypeError, ValueError):
        return False
    return expiry_date < datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _finite_positive(value):
    try:
        number = float(value)
        return number if number > 0 and number < float("inf") else None
    except (TypeError, ValueError, OverflowError):
        return None


def _is_long(direction):
    return str(direction or "").strip().upper() == "LONG"


def _entry_sides(direction):
    long = _is_long(direction)
    return {
        "low_call": "BUY" if long else "SELL",
        "low_put": "SELL" if long else "BUY",
        "high_call": "SELL" if long else "BUY",
        "high_put": "BUY" if long else "SELL",
    }


def _entry_prices(snapshot):
    sides = _entry_sides(snapshot.get("direction"))
    result = {}
    for leg, side in sides.items():
        result[leg] = _finite_positive(snapshot.get(f"{leg}_{'ask' if side == 'BUY' else 'bid'}"))
    return result


def _entry_depth(snapshot):
    sides = _entry_sides(snapshot.get("direction"))
    values = []
    for leg, side in sides.items():
        values.append(_finite_positive(snapshot.get(f"{leg}_{'ask' if side == 'BUY' else 'bid'}_qty")))
    return values


def _identity(snapshot):
    return "|".join(str(snapshot.get(key) or "") for key in (
        "symbol", "expiry", "timestamp_ns", "low_strike", "high_strike", "direction"
    ))


def _serialize_trace(row):
    entry = json.loads(row.entry_json)
    return {
        "id": row.id, "created_at": row.created_at.isoformat(),
        "symbol": row.symbol, "expiry": row.expiry, "direction": row.direction,
        "quantity_lots": row.quantity_lots, "lot_size": row.lot_size,
        "quantity": row.quantity_lots * row.lot_size,
        "entry": entry, "source_identity": row.source_identity,
    }


@router.get("")
def list_traces(limit: int = 200):
    limit = max(1, min(int(limit), 500))
    with SessionLocal() as db:
        rows = db.query(BoxSpreadTrace).order_by(BoxSpreadTrace.created_at.desc()).limit(limit).all()
        return {"strategy": "box-spread", "count": len(rows), "data": [_serialize_trace(row) for row in rows]}


@router.delete("/{trace_id}")
def delete_trace(trace_id: int):
    """Permanently remove one saved Box Spread trace and its exit-check history."""
    with SessionLocal() as db:
        row = db.query(BoxSpreadTrace).filter(BoxSpreadTrace.id == trace_id).first()
        if row is None:
            raise HTTPException(404, "Trace not found.")
        db.query(BoxSpreadTraceExitCheck).filter(
            BoxSpreadTraceExitCheck.trace_id == trace_id
        ).delete(synchronize_session=False)
        db.delete(row)
        db.commit()
        return {"strategy": "box-spread", "deleted": True, "trace_id": trace_id}


@router.post("")
def create_trace(payload: TraceCreate):
    snapshot = dict(payload.snapshot or {})
    required = ("symbol", "expiry", "direction", "low_strike", "high_strike", "timestamp_ns", "lot_size")
    if any(snapshot.get(key) in (None, "") for key in required):
        raise HTTPException(422, "Saved alert is missing its exact contract identity.")
    direction = str(snapshot.get("direction")).strip().upper()
    if direction not in {"LONG", "SHORT"}:
        raise HTTPException(422, "Direction must be LONG or SHORT.")
    prices = _entry_prices(snapshot)
    depths = _entry_depth(snapshot)
    if any(value is None for value in prices.values()) or any(value is None for value in depths):
        raise HTTPException(422, "All four saved executable entry prices and quantities are required.")
    try:
        lot_size = int(snapshot["lot_size"])
        if lot_size <= 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(422, "Invalid saved lot size.")
    max_lots = int(min(depths) // lot_size)
    quantity_lots = payload.quantity_lots if payload.quantity_lots is not None else max_lots
    if quantity_lots < 1 or quantity_lots > max_lots:
        raise HTTPException(422, {"message": "Requested trace quantity exceeds complete four-leg saved depth.", "max_complete_lots": max_lots})
    identity = _identity(snapshot)
    if not identity.strip("|"):
        raise HTTPException(422, "Alert identity is incomplete.")
    # Store the exact source row as immutable JSON; no later quote refresh mutates entry.
    snapshot["trace_entry_sides"] = _entry_sides(direction)
    snapshot["trace_entry_prices"] = prices
    snapshot["trace_entry_depth"] = depths
    snapshot["trace_quantity_lots"] = quantity_lots
    with SessionLocal() as db:
        existing = db.query(BoxSpreadTrace).filter(BoxSpreadTrace.source_identity == identity).first()
        if existing:
            return {"strategy": "box-spread", "created": False, "data": _serialize_trace(existing)}
        row = BoxSpreadTrace(
            source_identity=identity, symbol=str(snapshot["symbol"]).upper(),
            expiry=str(snapshot["expiry"]), direction=direction,
            quantity_lots=quantity_lots, lot_size=lot_size,
            entry_json=json.dumps(snapshot, separators=(",", ":"), default=str),
        )
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
            return {"strategy": "box-spread", "created": True, "data": _serialize_trace(row)}
        except IntegrityError:
            # Two identical Add-to-Trace requests can pass the initial lookup
            # concurrently. The unique source_identity index is authoritative;
            # recover the winner instead of surfacing a duplicate-click error.
            db.rollback()
            existing = (db.query(BoxSpreadTrace)
                        .filter(BoxSpreadTrace.source_identity == identity)
                        .first())
            if existing is not None:
                return {"strategy": "box-spread", "created": False, "data": _serialize_trace(existing)}
            raise


def _resolve_leg_instruments(entry):
    if not _master.instruments:
        _master.download()
    symbol = str(entry["symbol"]).strip().upper()
    exchange = "BFO" if symbol in {"SENSEX", "BANKEX"} else "NFO"
    expiry = _expiry_key(entry["expiry"])
    strikes = {"low_call": (float(entry["low_strike"]), "CE"),
               "low_put": (float(entry["low_strike"]), "PE"),
               "high_call": (float(entry["high_strike"]), "CE"),
               "high_put": (float(entry["high_strike"]), "PE")}
    descriptors = {}
    for leg, (strike, option_type) in strikes.items():
        matches = []
        for item in _master.instruments:
            if str(item.get("exch_seg", "")).strip().upper() != exchange:
                continue
            if str(item.get("name", "")).strip().upper() != symbol:
                continue
            if _expiry_key(item.get("expiry")) != expiry:
                continue
            if str(item.get("symbol", "")).strip().upper()[-2:] != option_type:
                continue
            try:
                item_strike = float(item.get("strike")) / 100.0
            except (TypeError, ValueError, OverflowError):
                continue
            if abs(item_strike - strike) < 0.0001 and str(item.get("token", "")).strip():
                matches.append(item)
        if len(matches) != 1:
            raise HTTPException(409, f"Cannot uniquely resolve {leg} contract from current instrument master.")
        item = matches[0]
        descriptors[leg] = CommonStrategyMarketFeed.descriptor(
            exchange=exchange, token=str(item["token"]), symbol=str(item["symbol"]),
            instrument_type="option", segment=exchange, expiry=str(item["expiry"]),
            strike=strike, option_type=option_type,
            lot_size=int(float(item.get("lotsize") or entry["lot_size"])),
            tick_size=float(item.get("tick_size") or 0.05), underlying=symbol,
        )
    return descriptors


def _exit_depth_levels(record, exit_side):
    """Return normalized executable levels from the live Angel One depth snapshot.

    Exit SELL consumes bids (buy side); exit BUY consumes asks (sell side).
    Angel One raw prices are in paise, while canonical record prices are rupees.
    """
    if record is None:
        return []
    side = "buy" if exit_side == "SELL" else "sell"
    payload = getattr(record, "payload", {})
    payload = payload if isinstance(payload, dict) else {}
    raw_levels = None
    for key in (f"best_5_{side}_data", f"best_{side}_data", f"best_{side}", side):
        candidate = payload.get(key)
        if isinstance(candidate, list) and candidate:
            raw_levels = candidate
            break
        if isinstance(candidate, dict):
            raw_levels = [candidate]
            break
    levels = []
    for level in raw_levels or []:
        if not isinstance(level, dict):
            continue
        raw_price = _finite_positive(level.get("price") or level.get(f"{side}_price"))
        raw_qty = _finite_positive(level.get("quantity") or level.get("qty") or level.get(f"{side}_quantity"))
        if raw_price is None or raw_qty is None:
            continue
        levels.append((raw_price / 100.0, raw_qty))
    if not levels:
        price = _finite_positive(record.bid if exit_side == "SELL" else record.ask)
        qty = _finite_positive(record.bid_qty if exit_side == "SELL" else record.ask_qty)
        if price is not None and qty is not None:
            levels = [(price, qty)]
    # Do not rely on provider ordering for executable price priority.
    levels.sort(key=lambda item: item[0], reverse=(exit_side == "SELL"))
    return levels


def _depth_vwap(levels, required_quantity):
    remaining = float(required_quantity)
    filled = 0.0
    notional = 0.0
    for price, quantity in levels:
        take = min(remaining, quantity)
        if take > 0:
            filled += take
            notional += take * price
            remaining -= take
        if remaining <= 0:
            break
    return (notional / filled if filled > 0 else None), filled


def _exit_valuation(trace, quotes):
    entry = json.loads(trace.entry_json)
    sides = _entry_sides(trace.direction)
    qty = trace.quantity_lots * trace.lot_size
    legs = []
    gross = 0.0
    available_lots = []
    all_fresh = True
    for leg in _leg_names:
        record = quotes.get(leg)
        entry_side = sides[leg]
        exit_side = "SELL" if entry_side == "BUY" else "BUY"
        now_ns = time.time_ns()
        quote_age = None if record is None else (now_ns - int(record.timestamp_ns)) / 1e9
        fresh = record is not None and quote_age is not None and -1.0 <= quote_age <= 5.0
        if quote_age is not None:
            quote_age = max(0.0, quote_age)
        all_fresh = all_fresh and fresh
        levels = _exit_depth_levels(record, exit_side) if fresh else []
        total_depth = sum(level[1] for level in levels)
        executable_price, executable_qty = _depth_vwap(levels, qty)
        complete_lots = int(total_depth // trace.lot_size)
        available_lots.append(complete_lots)
        entry_price = _finite_positive(entry.get("trace_entry_prices", {}).get(leg))
        leg_pnl = None
        if executable_price is not None and entry_price is not None:
            pnl_quantity = min(qty, executable_qty)
            leg_pnl = ((executable_price - entry_price) if entry_side == "BUY" else (entry_price - executable_price)) * pnl_quantity
            gross += leg_pnl
        best_price = levels[0][0] if levels else None
        legs.append({
            "leg": leg, "entry_side": entry_side, "entry_price": entry_price,
            "exit_side": exit_side, "exit_price": executable_price,
            "best_exit_price": best_price, "exit_qty": total_depth,
            "executable_quantity": executable_qty,
            "quote_age_seconds": None if quote_age is None else round(quote_age, 3),
            "fresh": fresh, "leg_gross_pnl": leg_pnl,
            "required_quantity": qty,
            "can_fill_required_quantity": bool(fresh and executable_price is not None and executable_qty >= qty),
            "depth_levels": [{"price": price, "quantity": quantity} for price, quantity in levels],
        })
    min_lots = min(available_lots) if available_lots else 0
    can_exit = all_fresh and all(item["can_fill_required_quantity"] for item in legs)
    status = "EXITABLE" if can_exit else "PARTIAL_DEPTH" if all_fresh and min_lots > 0 else "NOT_EXITABLE"
    # Recompute the current directional Box Edge from fresh executable bid/ask,
    # using the same four-leg formula as the scanner. Never fall back to LTP.
    latest_edge = None
    low_strike = _finite_positive(entry.get("low_strike"))
    high_strike = _finite_positive(entry.get("high_strike"))
    if (all_fresh and all(quotes.get(leg) is not None for leg in _leg_names)
            and low_strike is not None and high_strike is not None
            and high_strike > low_strike):
        low_call, low_put = quotes["low_call"], quotes["low_put"]
        high_call, high_put = quotes["high_call"], quotes["high_put"]
        gap = high_strike - low_strike
        if trace.direction == "LONG":
            values = (low_call.ask, high_put.ask, low_put.bid, high_call.bid)
            if all(_finite_positive(value) is not None for value in values):
                latest_edge = gap - ((float(low_call.ask) + float(high_put.ask)) -
                                     (float(low_put.bid) + float(high_call.bid)))
        elif trace.direction == "SHORT":
            values = (low_call.bid, high_put.bid, low_put.ask, high_call.ask)
            if all(_finite_positive(value) is not None for value in values):
                latest_edge = ((float(low_call.bid) + float(high_put.bid)) -
                               (float(low_put.ask) + float(high_call.ask))) - gap
    return {
        "status": status, "checked_at": datetime.now(timezone.utc).isoformat(),
        "entry_edge": _finite_positive(entry.get("executable_edge")),
        "latest_edge": round(latest_edge, 4) if latest_edge is not None else None,
        "exit_edge": round(gross / qty, 4) if can_exit and qty > 0 and all(item["leg_gross_pnl"] is not None for item in legs) else None,
        "trace_id": trace.id, "direction": trace.direction,
        "quantity_lots": trace.quantity_lots, "quantity": qty, "lot_size": trace.lot_size,
        "gross_pnl": round(gross, 2) if can_exit and all(item["leg_gross_pnl"] is not None for item in legs) else None,
        "gross_pnl_per_lot": round(gross / trace.quantity_lots, 2) if can_exit and trace.quantity_lots > 0 and all(item["leg_gross_pnl"] is not None for item in legs) else None,
        "max_complete_exit_lots_now": min_lots,
        "can_exit_full_quantity_now": can_exit,
        "message": ("All four legs have fresh executable depth for the traced quantity." if can_exit else
                    "Current market depth/quotes do not support a verified all-four-leg exit at the traced quantity."),
        "legs": legs, "orders_placed": False,
    }

@router.delete("/{trace_id}")
def delete_trace(trace_id: int):
    """Delete one Box Spread trace and its trace-only exit-check history."""
    with SessionLocal() as db:
        trace = db.query(BoxSpreadTrace).filter(BoxSpreadTrace.id == trace_id).first()
        if trace is None:
            raise HTTPException(404, "Trace not found.")
        db.query(BoxSpreadTraceExitCheck).filter(
            BoxSpreadTraceExitCheck.trace_id == trace_id
        ).delete(synchronize_session=False)
        db.delete(trace)
        db.commit()
    return {"strategy": "box-spread", "deleted": True, "trace_id": trace_id, "orders_placed": False}


@router.get("/{trace_id}/exit-checks")
def list_trace_exit_checks(trace_id: int, limit: int = 50):
    limit = max(1, min(int(limit), 200))
    with SessionLocal() as db:
        trace = db.query(BoxSpreadTrace).filter(BoxSpreadTrace.id == trace_id).first()
        if trace is None:
            raise HTTPException(404, "Trace not found.")
        rows = (db.query(BoxSpreadTraceExitCheck)
                .filter(BoxSpreadTraceExitCheck.trace_id == trace_id)
                .order_by(BoxSpreadTraceExitCheck.checked_at.desc())
                .limit(limit).all())
        return {"strategy": "box-spread", "trace_id": trace_id, "count": len(rows),
                "data": [{"id": row.id, "checked_at": row.checked_at.isoformat(),
                          "status": row.status, "gross_pnl": row.gross_pnl,
                          "result": json.loads(row.exit_json)} for row in rows]}


@router.post("/{trace_id}/exit-check")
def check_trace_exit(trace_id: int):
    with SessionLocal() as db:
        trace = db.query(BoxSpreadTrace).filter(BoxSpreadTrace.id == trace_id).first()
        if trace is None:
            raise HTTPException(404, "Trace not found.")
        entry = json.loads(trace.entry_json)
    if _trace_expired(trace.expiry):
        return {
            "strategy": "box-spread", "trace_id": trace.id, "status": "EXPIRED",
            "expired": True, "checked_at": datetime.now(timezone.utc).isoformat(),
            "entry_edge": _finite_positive(entry.get("executable_edge")),
            "latest_edge": None, "exit_edge": None, "gross_pnl": None,
            "gross_pnl_per_lot": None, "max_complete_exit_lots_now": 0,
            "can_exit_full_quantity_now": False,
            "message": "Contract expiry date has passed. This trace is historical and cannot be checked for a live exit.",
            "legs": [], "orders_placed": False,
        }
    descriptors = _resolve_leg_instruments(entry)
    manager = shared_common_manager()
    consumer = f"box-trace-exit-{trace_id}-{time.time_ns()}"
    quotes = {}
    reverse = {}
    for leg, descriptor in descriptors.items():
        reverse[(descriptor.key.exchange, descriptor.key.token)] = leg
    feed = CommonStrategyMarketFeed(consumer, manager=manager, mode=3)
    def on_record(record):
        key = (record.instrument.exchange, record.instrument.token)
        leg = reverse.get(key)
        if leg:
            with _lock:
                current = _latest.get(f"{consumer}:{leg}")
                if current is None or record.timestamp_ns >= current.timestamp_ns:
                    _latest[f"{consumer}:{leg}"] = record
    try:
        feed.start(descriptors.values(), on_record)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with _lock:
                quotes = {leg: _latest.get(f"{consumer}:{leg}") for leg in _leg_names}
            if all(record is not None and time.time_ns() - int(record.timestamp_ns) <= 5_000_000_000 for record in quotes.values()):
                break
            time.sleep(0.1)
    finally:
        feed.stop()
        with _lock:
            for leg in _leg_names:
                _latest.pop(f"{consumer}:{leg}", None)
    result = _exit_valuation(trace, quotes)
    with SessionLocal() as db:
        db.add(BoxSpreadTraceExitCheck(
            trace_id=trace.id, status=result["status"], gross_pnl=result["gross_pnl"],
            exit_json=json.dumps(result, separators=(",", ":"), default=str),
        ))
        db.commit()
    return {"strategy": "box-spread", **result}
