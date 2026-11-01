"""Read-only Box Spread trade trace and live exit valuation endpoints.

This module never places, modifies, or cancels broker orders.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from threading import Lock
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.core.database import SessionLocal
from app.models.box_spread_trace import BoxSpreadTrace, BoxSpreadTraceExitCheck
from app.market_data.instruments import InstrumentMaster
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.common_websocket import shared_common_manager

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
        db.commit()
        db.refresh(row)
        return {"strategy": "box-spread", "created": True, "data": _serialize_trace(row)}


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
        quote_age = None if record is None else max(0.0, (time.time_ns() - int(record.timestamp_ns)) / 1e9)
        fresh = record is not None and quote_age is not None and quote_age <= 5.0
        all_fresh = all_fresh and fresh
        price = None if not fresh else (record.bid if exit_side == "SELL" else record.ask)
        depth = None if not fresh else (record.bid_qty if exit_side == "SELL" else record.ask_qty)
        price = _finite_positive(price)
        depth = _finite_positive(depth)
        complete_lots = 0 if depth is None else int(depth // trace.lot_size)
        available_lots.append(complete_lots)
        entry_price = _finite_positive(entry.get("trace_entry_prices", {}).get(leg))
        leg_pnl = None if price is None or entry_price is None else ((price - entry_price) if entry_side == "BUY" else (entry_price - price)) * qty
        if leg_pnl is not None:
            gross += leg_pnl
        legs.append({
            "leg": leg, "entry_side": entry_side, "entry_price": entry_price,
            "exit_side": exit_side, "exit_price": price, "exit_qty": depth,
            "quote_age_seconds": None if quote_age is None else round(quote_age, 3),
            "fresh": fresh, "leg_gross_pnl": leg_pnl,
            "required_quantity": qty, "can_fill_required_quantity": bool(fresh and price is not None and depth is not None and depth >= qty),
        })
    min_lots = min(available_lots) if available_lots else 0
    can_exit = all_fresh and all(item["can_fill_required_quantity"] for item in legs)
    status = "EXITABLE" if can_exit else "PARTIAL_DEPTH" if all_fresh and min_lots > 0 else "NOT_EXITABLE"
    return {
        "status": status, "checked_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace.id, "direction": trace.direction,
        "quantity_lots": trace.quantity_lots, "quantity": qty, "lot_size": trace.lot_size,
        "gross_pnl": round(gross, 2) if all(item["leg_gross_pnl"] is not None for item in legs) else None,
        "max_complete_exit_lots_now": min_lots,
        "can_exit_full_quantity_now": can_exit,
        "message": ("All four legs have fresh executable depth for the traced quantity." if can_exit else
                    "Current market depth/quotes do not support a verified all-four-leg exit at the traced quantity."),
        "legs": legs, "orders_placed": False,
    }


@router.post("/{trace_id}/exit-check")
def check_trace_exit(trace_id: int):
    with SessionLocal() as db:
        trace = db.query(BoxSpreadTrace).filter(BoxSpreadTrace.id == trace_id).first()
        if trace is None:
            raise HTTPException(404, "Trace not found.")
        entry = json.loads(trace.entry_json)
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
