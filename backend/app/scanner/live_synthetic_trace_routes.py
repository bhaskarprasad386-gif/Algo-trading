"""On-demand exact-contract live status checks for user-traced synthetic opportunities.

This endpoint is intentionally request-driven: it makes one bounded REST FULL-quote
request for the exact future/CE/PE contract trio and never alters scanner subscriptions.
"""
from datetime import datetime
from math import isfinite
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/scanner/synthetic-cash-carry/trace", tags=["Synthetic Trace"])

_ALLOWED_INDEXES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
_EXCHANGE_BY_INDEX = {"SENSEX": "BFO", "BANKEX": "BFO"}
_EXCHANGE_TYPE = {"NFO": 2, "BFO": 4}


class TraceStatusRequest(BaseModel):
    underlying: str = Field(min_length=1, max_length=32)
    expiry: str = Field(min_length=8, max_length=32)
    strike: float = Field(gt=0, le=10_000_000)
    direction: Literal["LONG", "SHORT"]
    entry_edge: float


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
    calls = [
        item for item in options
        if str(item.get("symbol", "")).strip().upper().endswith("CE")
        and abs(_master_strike(item.get("strike")) - strike) < 1e-6
    ]
    puts = [
        item for item in options
        if str(item.get("symbol", "")).strip().upper().endswith("PE")
        and abs(_master_strike(item.get("strike")) - strike) < 1e-6
    ]
    matches = [("future", futures), ("call", calls), ("put", puts)]
    resolved = {}
    for role, candidates in matches:
        candidates = [item for item in candidates if str(item.get("token", "")).strip() and str(item.get("symbol", "")).strip()]
        if len(candidates) != 1:
            reason = "not found" if not candidates else "ambiguous in instrument master"
            raise LookupError(f"Exact {role} contract {reason} for {symbol} {target_expiry} strike {strike:g}")
        resolved[role] = candidates[0]
    return segment, resolved


def _quote_rows(response):
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict):
        fetched = data.get("fetched")
        if isinstance(fetched, list):
            return fetched
    if isinstance(data, list):
        return data
    return []


@router.post("/live-status")
def check_trace_live_status(request: TraceStatusRequest):
    """Fetch exact traced-contract depth once, only when the user clicks Check."""
    symbol = request.underlying.strip().upper()
    if symbol not in _ALLOWED_INDEXES:
        raise HTTPException(status_code=422, detail="Trace status supports the six configured indices only")
    try:
        from app.market_data.instruments import InstrumentMaster
        from app.market_data.client import MarketDataClient

        master = InstrumentMaster()
        master.download()
        segment, contracts = _resolve_contracts(master, symbol, request.expiry, float(request.strike))
        client = MarketDataClient()
        response = client.quote_many(segment, [
            {"tradingsymbol": str(contracts[role]["symbol"]), "symboltoken": str(contracts[role]["token"])}
            for role in ("future", "call", "put")
        ])
        quote_by_token = {
            str(item.get("symbolToken", item.get("symboltoken", ""))).strip(): item
            for item in _quote_rows(response) if isinstance(item, dict)
        }
        legs = {}
        for role, instrument in contracts.items():
            quote = quote_by_token.get(str(instrument["token"]).strip())
            if quote is None:
                raise LookupError(f"Live quote missing for exact {role} contract {instrument['symbol']}")
            legs[role] = {
                "symbol": str(instrument["symbol"]),
                "bid": _depth_side(quote, "bid"),
                "ask": _depth_side(quote, "ask"),
                "ltp": _positive_price(quote.get("ltp")),
            }
        future, call, put = legs["future"], legs["call"], legs["put"]
        if any(legs[role][side] is None for role in legs for side in ("bid", "ask")):
            raise LookupError("One or more exact contracts have no valid bid/ask depth; executable edge is unavailable")
        strike = float(request.strike)
        synthetic_bid = strike + call["bid"] - put["ask"]
        synthetic_ask = strike + call["ask"] - put["bid"]
        long_edge = future["bid"] - synthetic_ask
        short_edge = synthetic_bid - future["ask"]
        current_edge = long_edge if request.direction == "LONG" else short_edge
        entry_edge = float(request.entry_edge)
        if not isfinite(entry_edge):
            raise ValueError("Entry edge must be finite")
        return {
            "status": "LIVE_QUOTES",
            "underlying": symbol,
            "expiry": _expiry_date(request.expiry).isoformat(),
            "strike": strike,
            "direction": request.direction,
            "checked_at": datetime.now().astimezone().isoformat(),
            "exchange": segment,
            "contracts": legs,
            "synthetic_bid": synthetic_bid,
            "synthetic_ask": synthetic_ask,
            "long_edge": long_edge,
            "short_edge": short_edge,
            "entry_edge": entry_edge,
            "current_edge": current_edge,
            "edge_change": current_edge - entry_edge,
            "quote_source": "Angel One FULL quote depth",
            "broker_orders": "OFF",
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Exact live quote request failed: " + type(exc).__name__) from exc
