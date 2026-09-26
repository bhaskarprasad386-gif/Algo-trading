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
from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future
from app.backtesting.daily_gap import build_daily_gap_observations
from app.scanner.live_cash_future_scanner import LiveCashFutureScanner

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


def _stored_live_observations(db: Session, symbols: list[str], config: CashFutureConfig, max_age_seconds: float | None) -> tuple[list[dict], list[dict]]:
    """Read the latest persisted live observations; never call the broker."""
    now = datetime.now(IST).replace(tzinfo=None)
    cutoff = None if max_age_seconds is None else now - timedelta(seconds=float(max_age_seconds))
    stmt = select(CashFutureHistory).where(CashFutureHistory.symbol.in_(symbols), CashFutureHistory.timestamp <= now)
    if cutoff is not None:
        stmt = stmt.where(CashFutureHistory.timestamp >= cutoff)
    rows = db.scalars(stmt.order_by(CashFutureHistory.timestamp.desc())).all()
    latest: dict[tuple[str, str], CashFutureHistory] = {}
    for row in rows:
        latest.setdefault((row.symbol.upper(), row.contract_month), row)
    data: list[dict] = []
    errors: list[dict] = []
    for row in latest.values():
        try:
            result = calculate_cash_future(
                CashQuote(symbol=row.symbol, ltp=row.cash_price, bid=row.cash_bid, ask=row.cash_ask),
                FutureQuote(symbol=row.symbol, contract_month=row.contract_month, ltp=row.future_price,
                            lot_size=int(row.lot_size), margin_required=row.margin_required,
                            volume=int(row.volume or 0), oi=int(row.oi or 0), bid=row.future_bid,
                            ask=row.future_ask, expiry=row.expiry_date),
                config,
            )
            item = result.__dict__.copy()
            item.update({"timestamp": row.timestamp.isoformat(), "source": "stored-live-feed",
                         "cash_bid_qty": row.cash_bid_qty, "cash_ask_qty": row.cash_ask_qty,
                         "future_bid_qty": row.future_bid_qty, "future_ask_qty": row.future_ask_qty,
                         "volume": row.volume, "oi": row.oi})
            data.append(item)
        except Exception as exc:
            errors.append({"symbol": row.symbol, "contract_month": row.contract_month, "error": str(exc)})
    return data, errors


def _filtered(data: list[dict]) -> list[dict]:
    return [item for item in data if item.get("executable") is True]

@router.get("/cash-future/live/fast")
def cash_future_live_fast_scanner(
    max_age_seconds: float = Query(5.0, gt=0, le=30),
    limit: int = Query(50, ge=1, le=50),
):
    """Return signals produced directly by the one-second WebSocket scanner."""
    # The process-level scanner is installed by app.main; importing the singleton
    # here avoids a second market-data subscription.
    from app.main import live_cash_future_scanner
    return {
        "status": "success",
        "scanner": "cash-future",
        "mode": "live-fast",
        "data": live_cash_future_scanner.snapshot(max_age_seconds=max_age_seconds, limit=limit),
    }


@router.get("/cash-future/live/history")
def cash_future_live_scanner_history(
    days: int = Query(30, ge=1, le=30),
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """Return persisted eligible live scanner results for up to the last 30 days."""
    cutoff = datetime.now(IST).replace(tzinfo=None) - timedelta(days=int(days))
    rows = db.scalars(
        select(LiveCashFutureScannerResult)
        .where(LiveCashFutureScannerResult.observed_at >= cutoff)
        .order_by(LiveCashFutureScannerResult.observed_at.desc())
        .limit(int(limit))
    ).all()
    return {
        "status": "success",
        "scanner": "cash-future",
        "mode": "live-result-history",
        "days": int(days),
        "count": len(rows),
        "data": [
            {
                **{k: v for k, v in row.__dict__.items() if not k.startswith("_")},
                "reason_codes": tuple(filter(None, row.reason_codes.split(","))),
            }
            for row in rows
        ],
    }


@router.get("/cash-future/live/alerts")
def cash_future_live_alert_history(
    days: int = Query(30, ge=1, le=30),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """Return scanner-generated customized alerts retained for up to 30 days."""
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
    stored_data, errors = _stored_live_observations(db, symbols, config, max_quote_age_seconds)
    opportunities = _filtered(stored_data)
    opportunities.sort(key=lambda item: (-float(item.get("net_profit", 0)), -float(item.get("roi_pct", 0)), str(item.get("symbol", ""))))
    return {
        "status": "success",
        "scanner": "cash-future",
        "mode": "live-auto",
        "symbols_requested": symbols,
        "scanned_observations": len(stored_data),
        "opportunity_count": len(opportunities),
        "data": opportunities,
        "errors": errors,
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
