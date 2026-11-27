from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.market_data.instruments import InstrumentMaster
from app.models.cash_future_history import CashFutureHistory
from app.models.live_cash_future_scanner_result import LiveCashFutureScannerResult
from app.models.live_cash_future_alert_history import LiveCashFutureAlertHistory
from app.backtesting.daily_gap import build_daily_gap_observations

router = APIRouter(prefix="/api/v1/scanner", tags=["Scanner"])
IST = ZoneInfo("Asia/Kolkata")


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


@router.get("/cash-future/live/fast")
def cash_future_live_fast_scanner():
    """Retired endpoint: Cash-Future fast scanner API is intentionally disabled."""
    raise HTTPException(status_code=410, detail="Cash-Future live API is disabled")

@router.get("/cash-future/live/pairs")
def cash_future_live_pairs():
    """Retired endpoint: Cash-Future pair diagnostics API is intentionally disabled."""
    raise HTTPException(status_code=410, detail="Cash-Future live API is disabled")

@router.get("/cash-future/live/history")
def cash_future_live_scanner_history():
    """Retired endpoint: Cash-Future history reads are intentionally disabled."""
    raise HTTPException(status_code=410, detail="Cash-Future history API is disabled")

@router.get("/cash-future/live/alerts")
def cash_future_live_alert_history(
    days: int = Query(30, ge=1, le=30),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """Return scanner-generated customized alerts retained for up to 90 days."""
    cutoff = datetime.now(IST).replace(tzinfo=None) - timedelta(days=int(days))
    rows = db.scalars(
        select(LiveCashFutureAlertHistory)
        .where(LiveCashFutureAlertHistory.observed_at >= cutoff)
        .order_by(LiveCashFutureAlertHistory.observed_at.desc())
        .limit(int(limit))
    ).all()
    return {
        "status": "success",
        "scanner": "cash-future",
        "mode": "live-alert-history",
        "days": int(days),
        "count": len(rows),
        "data": [
            {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
            for row in rows
        ],
    }


@router.get("/cash-future/live/auto")
def cash_future_live_auto_scanner():
    """Retired endpoint: Cash-Future auto ranking/calculation is disabled."""
    raise HTTPException(status_code=410, detail="Cash-Future live API is disabled")


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
