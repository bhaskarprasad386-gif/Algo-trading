from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.market_data.instruments import InstrumentMaster
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
        if str(item.get("instrumenttype", "")).upper() not in {"FUTSTK", "FUTIDX"}:
            continue
        expiry = _expiry(item.get("expiry"))
        if not expiry or expiry < today:
            continue
        name = str(item.get("name", "")).strip().upper()
        if name:
            symbols.add(name)
    return sorted(symbols)[:limit]


@router.get("/cash-future/live/auto")
def cash_future_live_auto_scanner(
    limit: int = Query(50, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Automatically discover active NFO futures and scan their NSE cash pairs."""
    symbols = discover_cash_future_symbols(limit)
    if not symbols:
        raise HTTPException(status_code=404, detail="no active cash-future symbols found")
    result = CashFutureHistoryCollector(symbols).collect(db)
    return {
        "status": "success",
        "scanner": "cash-future",
        "mode": "live-auto",
        "symbols_requested": symbols,
        "count": len(result["collected"]),
        "data": result["collected"],
        "errors": result["errors"],
    }
