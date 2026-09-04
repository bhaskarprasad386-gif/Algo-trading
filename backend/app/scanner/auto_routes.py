from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.market_data.instruments import InstrumentMaster
from app.scanner.cash_future import CashFutureConfig
from app.scanner.cash_future_collector import CashFutureHistoryCollector

router = APIRouter(prefix="/api/v1/scanner", tags=["Scanner"])


def _expiry(value: object) -> date | None:
    if not value:
        return None
    text = str(value).strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _plain(value):
    """Normalize FastAPI Query defaults when the route is invoked directly in tests."""
    return getattr(value, "default", value)


def discover_cash_future_symbols(limit: int = 50) -> list[str]:
    limit = _plain(limit)
    master = InstrumentMaster()
    master.search(exchange="NFO")
    today = date.today()
    symbols: set[str] = set()
    for item in master.instruments:
        if str(item.get("exch_seg", "")).upper() != "NFO":
            continue
        if str(item.get("instrumenttype", "")).upper() != "FUTSTK":
            continue
        expiry = _expiry(item.get("expiry"))
        if not expiry or expiry < today:
            continue
        name = str(item.get("name", "")).strip().upper()
        if name:
            symbols.add(name)
    return sorted(symbols)[:int(limit)]


def _filtered(data: list[dict]) -> list[dict]:
    return [item for item in data if item.get("executable") is True]


@router.get("/cash-future/live/auto")
def cash_future_live_auto_scanner(
    limit: int = Query(50, ge=1, le=50),
    min_gap: float = Query(0.0),
    min_gap_pct: float = Query(0.0),
    min_net_profit: float = Query(0.0, ge=0),
    min_roi_pct: float = Query(0.0),
    min_volume: int = Query(0, ge=0),
    min_oi: int = Query(0, ge=0),
    max_bid_ask_spread_pct: float | None = Query(None, ge=0),
    max_cash_bid_ask_spread_pct: float | None = Query(None, ge=0),
    charges: float = Query(0.0, ge=0),
    funding_cost: float = Query(0.0, ge=0),
    max_quote_age_seconds: float | None = Query(15.0, gt=0),
    max_quote_timestamp_skew_seconds: float | None = Query(5.0, gt=0),
    db: Session = Depends(get_db),
):
    """Discover active stock futures and return only pairs passing execution checks."""
    limit = _plain(limit)
    min_gap = _plain(min_gap)
    min_gap_pct = _plain(min_gap_pct)
    min_net_profit = _plain(min_net_profit)
    min_roi_pct = _plain(min_roi_pct)
    min_volume = _plain(min_volume)
    min_oi = _plain(min_oi)
    max_bid_ask_spread_pct = _plain(max_bid_ask_spread_pct)
    max_cash_bid_ask_spread_pct = _plain(max_cash_bid_ask_spread_pct)
    charges = _plain(charges)
    funding_cost = _plain(funding_cost)
    max_quote_age_seconds = _plain(max_quote_age_seconds)
    max_quote_timestamp_skew_seconds = _plain(max_quote_timestamp_skew_seconds)

    symbols = discover_cash_future_symbols(limit)
    if not symbols:
        raise HTTPException(status_code=404, detail="no active cash-future symbols found")
    config = CashFutureConfig(
        min_gap=min_gap,
        min_gap_pct=min_gap_pct,
        min_net_profit=min_net_profit,
        min_roi_pct=min_roi_pct,
        min_volume=min_volume,
        min_oi=min_oi,
        max_bid_ask_spread_pct=max_bid_ask_spread_pct,
        max_cash_bid_ask_spread_pct=max_cash_bid_ask_spread_pct,
        charges=charges,
        funding_cost=funding_cost,
        require_two_sided_quotes=True,
    )
    result = CashFutureHistoryCollector(
        symbols,
        config=config,
        max_quote_age_seconds=max_quote_age_seconds,
        max_quote_timestamp_skew_seconds=max_quote_timestamp_skew_seconds,
    ).collect(db)
    opportunities = _filtered(result["collected"])
    opportunities.sort(key=lambda item: (item.get("net_profit", 0), item.get("roi_pct", 0)), reverse=True)
    return {
        "status": "success",
        "scanner": "cash-future",
        "mode": "live-auto",
        "symbols_requested": symbols,
        "scanned_observations": len(result["collected"]),
        "opportunity_count": len(opportunities),
        "data": opportunities,
        "errors": result["errors"],
        "filters": {
            "min_gap": min_gap,
            "min_gap_pct": min_gap_pct,
            "min_net_profit": min_net_profit,
            "min_roi_pct": min_roi_pct,
            "min_volume": min_volume,
            "min_oi": min_oi,
            "max_bid_ask_spread_pct": max_bid_ask_spread_pct,
            "max_cash_bid_ask_spread_pct": max_cash_bid_ask_spread_pct,
            "charges": charges,
            "funding_cost": funding_cost,
            "max_quote_age_seconds": max_quote_age_seconds,
            "max_quote_timestamp_skew_seconds": max_quote_timestamp_skew_seconds,
            "require_two_sided_quotes": True,
        },
    }
