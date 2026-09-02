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


def discover_cash_future_symbols(limit: int = 50) -> list[str]:
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
    return sorted(symbols)[:limit]


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
    db: Session = Depends(get_db),
):
    """Discover active stock futures and return only pairs passing requested filters."""
    symbols = discover_cash_future_symbols(limit)
    if not symbols:
        raise HTTPException(status_code=404, detail="no active cash-future symbols found")
    config = CashFutureConfig(
        min_gap=min_gap, min_gap_pct=min_gap_pct, min_net_profit=min_net_profit,
        min_roi_pct=min_roi_pct, min_volume=min_volume, min_oi=min_oi,
        max_bid_ask_spread_pct=max_bid_ask_spread_pct,
    )
    result = CashFutureHistoryCollector(symbols, config=config).collect(db)
    opportunities = _filtered(result["collected"])
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
            "min_gap": min_gap, "min_gap_pct": min_gap_pct,
            "min_net_profit": min_net_profit, "min_roi_pct": min_roi_pct,
            "min_volume": min_volume, "min_oi": min_oi,
            "max_bid_ask_spread_pct": max_bid_ask_spread_pct,
        },
    }
