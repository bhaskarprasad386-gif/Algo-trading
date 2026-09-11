from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.market_data.instruments import InstrumentMaster
from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future import CashFutureConfig
from app.scanner.cash_future_collector import CashFutureHistoryCollector
from app.backtesting.daily_gap import build_daily_gap_observations

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


@router.get("/cash-future/calendar/{trading_date}/top-gap")
def cash_future_calendar_top_gap(
    trading_date: date,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Return top opening gaps for one historical trading date.

    Ranking is by absolute opening gap in cash price multiplied by the
    historical futures lot size. The response includes previous close,
    open, high, low and close for the selected date.
    """
    start = datetime.combine(trading_date, datetime.min.time())
    end = start + timedelta(days=1)
    rows = db.scalars(
        select(CashFutureHistory)
        .where(CashFutureHistory.timestamp >= start, CashFutureHistory.timestamp < end)
        .order_by(CashFutureHistory.symbol, CashFutureHistory.timestamp)
    ).all()
    if not rows:
        return {"status": "success", "trading_date": trading_date.isoformat(), "top": None, "data": []}

    symbols = sorted({row.symbol for row in rows})
    previous_ts = dict(
        db.execute(
            select(CashFutureHistory.symbol, func.max(CashFutureHistory.timestamp))
            .where(CashFutureHistory.symbol.in_(symbols), CashFutureHistory.timestamp < start)
            .group_by(CashFutureHistory.symbol)
        ).all()
    )
    if not previous_ts:
        return {"status": "success", "trading_date": trading_date.isoformat(), "top": None, "data": []}

    previous_rows = db.scalars(
        select(CashFutureHistory).where(
            and_(
                CashFutureHistory.symbol.in_(symbols),
                CashFutureHistory.timestamp.in_(tuple(previous_ts.values())),
            )
        )
    ).all()
    previous_close = {row.symbol: row.cash_price for row in previous_rows}

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        if row.symbol not in previous_close:
            continue
        item = grouped.get(row.symbol)
        if item is None:
            item = {
                "trading_date": trading_date,
                "symbol": row.symbol,
                "previous_close": previous_close[row.symbol],
                "open": row.cash_price,
                "high": row.cash_price,
                "low": row.cash_price,
                "close": row.cash_price,
                "lot_size": row.lot_size,
                "first_timestamp": row.timestamp,
                "last_timestamp": row.timestamp,
            }
            grouped[row.symbol] = item
        else:
            item["high"] = max(float(item["high"]), row.cash_price)
            item["low"] = min(float(item["low"]), row.cash_price)
            if row.timestamp < item["first_timestamp"]:
                item["first_timestamp"] = row.timestamp
                item["open"] = row.cash_price
            if row.timestamp >= item["last_timestamp"]:
                item["last_timestamp"] = row.timestamp
                item["close"] = row.cash_price

    observations = build_daily_gap_observations(grouped.values())
    ranked = sorted(observations, key=lambda row: (row.weighted_gap, row.symbol), reverse=True)[:limit]
    data = [
        {
            "trading_date": row.trading_date.isoformat(),
            "symbol": row.symbol,
            "direction": row.direction,
            "gap": row.gap,
            "gap_percent": row.gap_percent,
            "weighted_gap": row.weighted_gap,
            "previous_close": row.previous_close,
            "open": row.open_price,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "lot_size": row.lot_size,
        }
        for row in ranked
    ]
    return {
        "status": "success",
        "trading_date": trading_date.isoformat(),
        "top": data[0] if data else None,
        "data": data,
    }
