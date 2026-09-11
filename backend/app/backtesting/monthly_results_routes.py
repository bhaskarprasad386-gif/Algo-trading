"""Advanced Results APIs for date/month gap discovery and OHLC graphs."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(prefix="/api/v1/backtesting/results", tags=["Backtesting Results"])


def _daily_rows(db: Session, start: date, end: date, symbol: str | None = None, instrument_type: str = "STOCK") -> list[dict]:
    params: dict[str, object] = {"start": start.isoformat(), "end": (end + timedelta(days=1)).isoformat(), "instrument_type": instrument_type.upper()}
    symbol_filter = ""
    if symbol:
        symbol_filter = " AND symbol = :symbol"
        params["symbol"] = symbol.strip().upper()
    sql = text(
        """
        WITH base AS (
            SELECT instrument_key, symbol, segment, instrument_type, contract_month,
                   date(timestamp) AS trading_date, timestamp, open, high, low, close, lot_size
            FROM historical_market_bars
            WHERE timestamp >= :start AND timestamp < :end
              AND upper(instrument_type) = :instrument_type
        """ + symbol_filter + """
        ), daily AS (
            SELECT instrument_key, symbol, segment, instrument_type, contract_month, trading_date,
                   MIN(timestamp) first_ts, MAX(timestamp) last_ts,
                   MAX(high) high, MIN(low) low, MAX(lot_size) lot_size
            FROM base
            GROUP BY instrument_key, symbol, segment, instrument_type, contract_month, trading_date
        ), daily_ohlc AS (
            SELECT d.*, firstbar.open open, lastbar.close close
            FROM daily d
            JOIN base firstbar ON firstbar.instrument_key=d.instrument_key AND firstbar.timestamp=d.first_ts
            JOIN base lastbar ON lastbar.instrument_key=d.instrument_key AND lastbar.timestamp=d.last_ts
        ), with_prev AS (
            SELECT *, LAG(close) OVER (PARTITION BY instrument_key ORDER BY trading_date) previous_close
            FROM daily_ohlc
        )
        SELECT instrument_key, symbol, segment, instrument_type, contract_month, trading_date,
               open, high, low, close, lot_size, previous_close
        FROM with_prev
        ORDER BY trading_date, symbol, instrument_key
        """
    )
    return [dict(row) for row in db.execute(sql, params).mappings().all()]


@router.get("/monthly-gap")
def monthly_gap_search(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    mode: str = Query("opening", pattern="^(opening|shorting)$"),
    instrument_type: str = Query("STOCK"),
    symbol: str | None = Query(None),
    contract_month: str | None = Query(None),
    db: Session = Depends(get_db),
):
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    rows = _daily_rows(db, start, end, symbol=symbol, instrument_type=instrument_type)
    candidates = []
    for row in rows:
        if contract_month and row["contract_month"] != contract_month:
            continue
        if not row["lot_size"]:
            continue
        if mode == "opening":
            if row["previous_close"] is None:
                continue
            gap = float(row["open"]) - float(row["previous_close"])
            gap_value = abs(gap) * float(row["lot_size"])
        else:
            gap = float(row["high"]) - float(row["open"])
            gap_value = gap * float(row["lot_size"])
        candidates.append((gap_value, row["trading_date"], row["symbol"], gap, row))
    if not candidates:
        raise HTTPException(status_code=404, detail="no historical OHLC rows found for the requested month")
    top = max(candidates, key=lambda x: (x[0], x[1], x[2]))
    _, trading_date, symbol_name, gap, row = top
    return {
        "status": "success", "month": f"{year:04d}-{month:02d}", "mode": mode,
        "instrument_type": instrument_type.upper(), "result": {
            "trading_date": trading_date, "symbol": symbol_name, "gap": gap,
            "gap_value": top[0], "open": row["open"], "high": row["high"], "low": row["low"],
            "close": row["close"], "lot_size": row["lot_size"], "previous_close": row["previous_close"],
            "contract_month": row["contract_month"],
        },
    }


@router.get("/monthly-graph")
def monthly_graph(
    symbol: str = Query(...),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    instrument_type: str = Query("STOCK"),
    contract_month: str | None = Query(None),
    db: Session = Depends(get_db),
):
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    rows = _daily_rows(db, start, end, symbol=symbol, instrument_type=instrument_type)
    rows = [r for r in rows if contract_month is None or r["contract_month"] == contract_month]
    if not rows:
        raise HTTPException(status_code=404, detail="no historical OHLC rows found for the requested symbol/month")
    return {
        "status": "success", "symbol": symbol.strip().upper(), "month": f"{year:04d}-{month:02d}",
        "instrument_type": instrument_type.upper(), "contract_month": contract_month,
        "count": len(rows),
        "series": [{"trading_date": r["trading_date"], "open": r["open"], "high": r["high"], "low": r["low"],
                    "close": r["close"], "lot_size": r["lot_size"], "contract_month": r["contract_month"]} for r in rows],
    }


@router.get("/monthly-symbols")
def monthly_symbols(
    year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12),
    instrument_type: str = Query("STOCK"), db: Session = Depends(get_db),
):
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    rows = _daily_rows(db, start, end, instrument_type=instrument_type)
    symbols = sorted({r["symbol"] for r in rows})
    return {"status": "success", "month": f"{year:04d}-{month:02d}", "instrument_type": instrument_type.upper(), "symbols": symbols, "count": len(symbols)}


@router.get("/intraday-replay")
def intraday_replay(
    trading_date: date = Query(...),
    symbol: str = Query(...),
    instrument_type: str = Query("STOCK"),
    contract_month: str | None = Query(None),
    interval_minutes: int = Query(1, ge=1, le=60),
    db: Session = Depends(get_db),
):
    """Return one-minute source bars for a date; Android replays them into 15m candles."""
    params: dict[str, object] = {
        "start": trading_date.isoformat(),
        "end": (trading_date + timedelta(days=1)).isoformat(),
        "symbol": symbol.strip().upper(),
        "instrument_type": instrument_type.upper(),
    }
    contract_filter = ""
    if contract_month:
        contract_filter = " AND contract_month = :contract_month"
        params["contract_month"] = contract_month
    sql = text(
        """
        SELECT timestamp, open, high, low, close, volume, oi, lot_size, contract_month, instrument_key
        FROM historical_market_bars
        WHERE timestamp >= :start AND timestamp < :end
          AND upper(symbol) = :symbol
          AND upper(instrument_type) = :instrument_type
        """ + contract_filter + """
        ORDER BY timestamp ASC, instrument_key ASC
        """
    )
    rows = [dict(row) for row in db.execute(sql, params).mappings().all()]
    if not rows:
        raise HTTPException(status_code=404, detail="no intraday historical data found for the requested date/symbol")
    return {
        "status": "success", "trading_date": trading_date, "symbol": symbol.strip().upper(),
        "instrument_type": instrument_type.upper(), "contract_month": contract_month,
        "source_interval_minutes": interval_minutes, "chart_interval_minutes": 15,
        "count": len(rows),
        "series": rows,
    }
