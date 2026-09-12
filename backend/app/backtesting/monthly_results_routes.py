"""Advanced Results APIs for date/month gap discovery and OHLC graphs."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.backtesting.cash_future_historical_loader import CashFutureHistoricalLoader, CashFutureHistorySelection
from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_catalog import HistoricalCatalog
from app.core.config import settings
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


def _gap_payload(row: dict, mode: str) -> dict:
    lot = float(row["lot_size"] or 0)
    if mode == "shorting":
        gap = float(row["high"]) - float(row["open"])
        gap_percent = gap / float(row["open"]) * 100.0 if row["open"] else 0.0
    else:
        previous = row["previous_close"]
        gap = float(row["open"]) - float(previous) if previous is not None else 0.0
        gap_percent = gap / float(previous) * 100.0 if previous else 0.0
    return {
        "trading_date": row["trading_date"], "symbol": row["symbol"],
        "direction": "UP" if gap > 0 else "DOWN" if gap < 0 else "FLAT",
        "gap": gap, "gap_percent": gap_percent, "weighted_gap": gap * lot,
        "previous_close": row["previous_close"] or 0.0, "open": row["open"], "high": row["high"],
        "low": row["low"], "close": row["close"], "lot_size": row["lot_size"],
        "contract_month": row["contract_month"], "instrument_key": row["instrument_key"],
    }


def _cash_future_shorting_payloads(
    trading_date: date,
    symbols: list[str],
    *,
    contract_month: str | None = None,
    source: str = "angelone",
    mode: str = "CURRENT",
) -> list[dict]:
    """Return the highest actual intraday Future-Cash gap for each symbol/day."""
    catalog = HistoricalCatalog(settings.BACKTEST_DATA_DB)
    contracts = ContractMasterCatalog(settings.BACKTEST_CONTRACT_DB)
    loader = CashFutureHistoricalLoader(catalog, contracts)
    result: list[dict] = []
    try:
        for symbol in sorted({value.strip().upper() for value in symbols if value and value.strip()}):
            selection = CashFutureHistorySelection(
                spot_instrument=symbol,
                exchange="NSE",
                underlying=symbol,
                start_date=trading_date,
                end_date=trading_date,
                timeframe="1m",
                contract_month=contract_month,
                mode=mode,
                source=source,
            )
            points = list(loader.iter_points(selection))
            if not points:
                continue
            top = max(points, key=lambda point: (point.gap, point.timestamp))
            if top.lot_size <= 0:
                continue
            result.append({
                "trading_date": trading_date,
                "symbol": symbol,
                "direction": "UP" if top.gap > 0 else "DOWN" if top.gap < 0 else "FLAT",
                "gap": top.gap,
                "gap_percent": top.gap_pct,
                "weighted_gap": top.gap * top.lot_size,
                "previous_close": 0.0,
                "open": top.cash_price,
                "high": top.future_price,
                "low": top.cash_price,
                "close": top.future_price,
                "lot_size": top.lot_size,
                "contract_month": top.contract_month,
                "instrument_key": f"NFO:{top.contract_month}",
                "gap_high_timestamp": top.timestamp.isoformat(),
                "cash_price_at_gap_high": top.cash_price,
                "future_price_at_gap_high": top.future_price,
            })
    finally:
        catalog.close()
        contracts.close()
    return result


@router.get("/date-gap")
def date_gap_ranking(
    trading_date: date = Query(...),
    mode: str = Query("shorting", pattern="^(opening|shorting)$"),
    instrument_type: str = Query("STOCK"),
    symbol: str | None = Query(None),
    contract_month: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    rows = _daily_rows(db, trading_date, trading_date, symbol=symbol, instrument_type=instrument_type)
    if mode == "shorting":
        payload = _cash_future_shorting_payloads(
            trading_date,
            [row["symbol"] for row in rows],
            contract_month=contract_month,
            mode="CURRENT",
        )
    else:
        payload = [_gap_payload(row, mode) for row in rows if row["lot_size"] and (not contract_month or row["contract_month"] == contract_month)]
    payload.sort(key=lambda item: (item["weighted_gap"], item["symbol"]), reverse=True)
    payload = payload[:limit]
    if not payload:
        raise HTTPException(status_code=404, detail="no historical Cash-Future gap rows found for the requested date")
    return {"status": "success", "trading_date": trading_date, "mode": mode, "instrument_type": instrument_type.upper(), "count": len(payload), "top": payload[0], "data": payload}


@router.get("/prior-gap")
def prior_gap_comparison(
    trading_date: date = Query(...),
    mode: str = Query("shorting", pattern="^(opening|shorting)$"),
    instrument_type: str = Query("STOCK"),
    symbol: str | None = Query(None),
    contract_month: str | None = Query(None),
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    selected_rows = _daily_rows(db, trading_date, trading_date, symbol=symbol, instrument_type=instrument_type)
    if mode == "shorting":
        selected = _cash_future_shorting_payloads(trading_date, [row["symbol"] for row in selected_rows], contract_month=contract_month)
        prior_rows = _daily_rows(db, date(2000, 1, 1), trading_date - timedelta(days=1), symbol=symbol, instrument_type=instrument_type)
        prior_by_day: dict[date, list[str]] = {}
        for row in prior_rows:
            prior_by_day.setdefault(row["trading_date"], []).append(row["symbol"])
        prior: list[dict] = []
        for prior_day, symbols in prior_by_day.items():
            prior.extend(_cash_future_shorting_payloads(prior_day, symbols, contract_month=contract_month))
    else:
        selected = [_gap_payload(row, mode) for row in selected_rows if row["lot_size"] and (not contract_month or row["contract_month"] == contract_month)]
        prior_rows = _daily_rows(db, date(2000, 1, 1), trading_date - timedelta(days=1), symbol=symbol, instrument_type=instrument_type)
        prior = [_gap_payload(row, mode) for row in prior_rows if row["lot_size"] and (not contract_month or row["contract_month"] == contract_month)]
    selected.sort(key=lambda item: (item["weighted_gap"], item["symbol"]), reverse=True)
    if not selected:
        raise HTTPException(status_code=404, detail="no historical gap rows found for the selected date")
    threshold = selected[0]["weighted_gap"]
    larger = [item for item in prior if item["weighted_gap"] > threshold]
    larger.sort(key=lambda item: (item["weighted_gap"], item["trading_date"], item["symbol"]), reverse=True)
    return {"status":"success","trading_date":trading_date,"mode":mode,"instrument_type":instrument_type.upper(),"selected":selected[0],"has_larger_prior_gap":bool(larger),"prior_larger":larger[:limit]}


@router.get("/monthly-gap")
def monthly_gap_search(
    year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12),
    mode: str = Query("opening", pattern="^(opening|shorting)$"), instrument_type: str = Query("STOCK"),
    symbol: str | None = Query(None), contract_month: str | None = Query(None), db: Session = Depends(get_db),
):
    start = date(year, month, 1); end = date(year, month, monthrange(year, month)[1])
    rows = _daily_rows(db, start, end, symbol=symbol, instrument_type=instrument_type)
    candidates = []
    if mode == "shorting":
        for trading_day in sorted({row["trading_date"] for row in rows}):
            day_rows = [row for row in rows if row["trading_date"] == trading_day]
            for item in _cash_future_shorting_payloads(trading_day, [row["symbol"] for row in day_rows], contract_month=contract_month):
                candidates.append((item["weighted_gap"], item["trading_date"], item["symbol"], item["gap"], item))
    else:
        for row in rows:
            if contract_month and row["contract_month"] != contract_month: continue
            if not row["lot_size"] or row["previous_close"] is None: continue
            gap = float(row["open"]) - float(row["previous_close"])
            candidates.append((abs(gap) * float(row["lot_size"]), row["trading_date"], row["symbol"], gap, row))
    if not candidates: raise HTTPException(status_code=404, detail="no historical gap rows found for the requested month")
    top = max(candidates, key=lambda x: (x[0], x[1], x[2]))
    if mode == "shorting":
        item = top[4]
        return {"status":"success","month":f"{year:04d}-{month:02d}","mode":mode,"instrument_type":instrument_type.upper(),"result":{"trading_date":item["trading_date"],"symbol":item["symbol"],"gap":item["gap"],"gap_value":item["weighted_gap"],"open":item["open"],"high":item["high"],"low":item["low"],"close":item["close"],"lot_size":item["lot_size"],"previous_close":item["previous_close"],"contract_month":item["contract_month"],"gap_high_timestamp":item.get("gap_high_timestamp"),"cash_price_at_gap_high":item.get("cash_price_at_gap_high"),"future_price_at_gap_high":item.get("future_price_at_gap_high")}}
    _, trading_date, symbol_name, gap, row = top
    return {"status":"success","month":f"{year:04d}-{month:02d}","mode":mode,"instrument_type":instrument_type.upper(),"result":{"trading_date":trading_date,"symbol":symbol_name,"gap":gap,"gap_value":top[0],"open":row["open"],"high":row["high"],"low":row["low"],"close":row["close"],"lot_size":row["lot_size"],"previous_close":row["previous_close"],"contract_month":row["contract_month"]}}


@router.get("/monthly-gap-top10")
def monthly_gap_top10(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    instrument_type: str = Query("STOCK"),
    contract_month: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Rank each stock once by its highest actual intraday Future-Cash gap in the month."""
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    rows = _daily_rows(db, start, end, instrument_type=instrument_type)
    if not rows:
        raise HTTPException(status_code=404, detail="no historical rows found for the requested month")

    monthly_highs: dict[str, dict] = {}
    for trading_day in sorted({row["trading_date"] for row in rows}):
        day_rows = [row for row in rows if row["trading_date"] == trading_day]
        day_payloads = _cash_future_shorting_payloads(
            trading_day,
            [row["symbol"] for row in day_rows],
            contract_month=contract_month,
            mode="CURRENT",
        )
        for item in day_payloads:
            symbol_name = item["symbol"]
            current = monthly_highs.get(symbol_name)
            if current is None or (item["weighted_gap"], item["gap_high_timestamp"]) > (current["weighted_gap"], current["gap_high_timestamp"]):
                monthly_highs[symbol_name] = item

    if not monthly_highs:
        raise HTTPException(status_code=404, detail="no historical Cash-Future gap rows found for the requested month")

    ranked = sorted(
        monthly_highs.values(),
        key=lambda item: (item["weighted_gap"], item["symbol"]),
        reverse=True,
    )[:10]
    data = []
    for rank, item in enumerate(ranked, start=1):
        timestamp = item.get("gap_high_timestamp")
        data.append({
            "rank": rank,
            "symbol": item["symbol"],
            "lot_size": item["lot_size"],
            "month_gap_high": item["gap"],
            "gap_value": item["weighted_gap"],
            "gap_high_date": item["trading_date"],
            "gap_high_time": timestamp.split("T", 1)[1] if timestamp and "T" in timestamp else timestamp,
            "gap_high_timestamp": timestamp,
            "cash_price_at_gap_high": item["cash_price_at_gap_high"],
            "future_price_at_gap_high": item["future_price_at_gap_high"],
            "contract_month": item["contract_month"],
            "instrument_key": item["instrument_key"],
        })

    return {
        "status": "success",
        "month": f"{year:04d}-{month:02d}",
        "mode": "shorting",
        "instrument_type": instrument_type.upper(),
        "count": len(data),
        "data": data,
    }


@router.get("/monthly-graph")
def monthly_graph(
    symbol: str = Query(...), year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12),
    instrument_type: str = Query("STOCK"), contract_month: str | None = Query(None), db: Session = Depends(get_db),
):
    start = date(year, month, 1); end = date(year, month, monthrange(year, month)[1])
    rows = _daily_rows(db, start, end, symbol=symbol, instrument_type=instrument_type)
    rows = [r for r in rows if contract_month is None or r["contract_month"] == contract_month]
    if not rows: raise HTTPException(status_code=404, detail="no historical OHLC rows found for the requested symbol/month")
    return {"status":"success","symbol":symbol.strip().upper(),"month":f"{year:04d}-{month:02d}","instrument_type":instrument_type.upper(),"contract_month":contract_month,"count":len(rows),"series":[{"trading_date":r["trading_date"],"open":r["open"],"high":r["high"],"low":r["low"],"close":r["close"],"lot_size":r["lot_size"],"contract_month":r["contract_month"]} for r in rows]}


@router.get("/monthly-symbols")
def monthly_symbols(year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12), instrument_type: str = Query("STOCK"), db: Session = Depends(get_db)):
    start = date(year, month, 1); end = date(year, month, monthrange(year, month)[1])
    rows = _daily_rows(db, start, end, instrument_type=instrument_type)
    symbols = sorted({r["symbol"] for r in rows})
    return {"status":"success","month":f"{year:04d}-{month:02d}","instrument_type":instrument_type.upper(),"symbols":symbols,"count":len(symbols)}


@router.get("/intraday-replay")
def intraday_replay(
    trading_date: date = Query(...), symbol: str = Query(...), instrument_type: str = Query("STOCK"),
    contract_month: str | None = Query(None), interval_minutes: int = Query(1, ge=1, le=60), db: Session = Depends(get_db),
):
    """Return one-minute source bars for a date; Android replays them into 15m candles."""
    params: dict[str, object] = {"start": trading_date.isoformat(), "end": (trading_date + timedelta(days=1)).isoformat(), "symbol": symbol.strip().upper(), "instrument_type": instrument_type.upper()}
    contract_filter = ""
    if contract_month:
        contract_filter = " AND contract_month = :contract_month"; params["contract_month"] = contract_month
    sql = text("""
        SELECT timestamp, open, high, low, close, volume, oi, lot_size, contract_month, instrument_key
        FROM historical_market_bars
        WHERE timestamp >= :start AND timestamp < :end
          AND upper(symbol) = :symbol AND upper(instrument_type) = :instrument_type
    """ + contract_filter + " ORDER BY timestamp ASC, instrument_key ASC")
    rows = [dict(row) for row in db.execute(sql, params).mappings().all()]
    if not rows: raise HTTPException(status_code=404, detail="no intraday historical data found for the requested date/symbol")
    return {"status":"success","trading_date":trading_date,"symbol":symbol.strip().upper(),"instrument_type":instrument_type.upper(),"contract_month":contract_month,"source_interval_minutes":interval_minutes,"chart_interval_minutes":15,"count":len(rows),"series":rows}
