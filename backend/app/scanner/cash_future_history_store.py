"""Database persistence/query helpers for Cash-Future history."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cash_future_history import CashFutureHistory
from app.scanner.cash_future_history import CashFutureHistoryPoint, build_graph_series, find_historical_gap_matches

IST = ZoneInfo("Asia/Kolkata")


def _naive_ist(value: datetime) -> datetime:
    """Normalize an observation/query datetime to naive IST for DB compatibility."""
    if value.tzinfo is not None:
        return value.astimezone(IST).replace(tzinfo=None)
    return value


def save_history_point(db: Session, point: CashFutureHistoryPoint, expiry_date: date | None = None) -> CashFutureHistory:
    """Insert or update one observation by symbol/contract/timestamp."""
    timestamp = _naive_ist(point.timestamp)
    existing = db.scalar(
        select(CashFutureHistory).where(
            CashFutureHistory.symbol == point.symbol.upper(),
            CashFutureHistory.contract_month == point.contract_month,
            CashFutureHistory.timestamp == timestamp,
        )
    )
    if existing is None:
        existing = CashFutureHistory(
            symbol=point.symbol.upper(),
            contract_month=point.contract_month,
            timestamp=timestamp,
        )
        db.add(existing)

    existing.cash_price = point.cash_price
    existing.future_price = point.future_price
    existing.gap = point.gap
    existing.gap_pct = point.gap_pct
    existing.lot_size = point.lot_size
    existing.margin_required = point.margin_required
    existing.charges = point.charges
    existing.funding_cost = point.funding_cost
    existing.net_profit = point.net_profit
    existing.roi_pct = point.roi_pct
    if expiry_date is not None:
        existing.expiry_date = expiry_date
    db.commit()
    db.refresh(existing)
    return existing


def read_history(
    db: Session,
    symbol: str,
    contract_month: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[CashFutureHistoryPoint]:
    stmt = select(CashFutureHistory).where(
        CashFutureHistory.symbol == symbol.upper(),
        CashFutureHistory.contract_month == contract_month,
    )
    if start is not None:
        stmt = stmt.where(CashFutureHistory.timestamp >= _naive_ist(start))
    if end is not None:
        stmt = stmt.where(CashFutureHistory.timestamp <= _naive_ist(end))
    rows = db.scalars(stmt.order_by(CashFutureHistory.timestamp)).all()
    return [
        CashFutureHistoryPoint(
            timestamp=r.timestamp,
            symbol=r.symbol,
            contract_month=r.contract_month,
            cash_price=r.cash_price,
            future_price=r.future_price,
            gap=r.gap,
            gap_pct=r.gap_pct,
            lot_size=r.lot_size,
            margin_required=r.margin_required,
            charges=r.charges,
            funding_cost=r.funding_cost,
            net_profit=r.net_profit,
            roi_pct=r.roi_pct,
            expiry_date=r.expiry_date,
        )
        for r in rows
    ]


def find_expiry_close(db: Session, symbol: str, contract_month: str, expiry_date: date) -> dict | None:
    """Return the last observation in the 15:20–15:30 IST expiry-day window."""
    start = datetime.combine(expiry_date, time(15, 20))
    end = datetime.combine(expiry_date, time(15, 30))
    points = read_history(db, symbol, contract_month, start, end)
    if not points:
        return None
    point = points[-1]
    return {
        "expiry_date": expiry_date.isoformat(),
        "close_time": point.timestamp.isoformat(),
        "cash_price": point.cash_price,
        "future_price": point.future_price,
        "closing_gap": point.gap,
        "closing_gap_pct": point.gap_pct,
        "gap_closed": point.gap == 0.0,
        "net_profit": point.net_profit,
        "roi_pct": point.roi_pct,
    }
