from datetime import date, datetime, timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logger import app_logger
from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future
from app.scanner.cash_future_backtest import BacktestConfig, run_backtest
from app.scanner.cash_future_collector import CashFutureHistoryCollector
from app.scanner.cash_future_history import (
    CashFutureHistoryPoint,
    analyze_historical_gap_outcomes,
    build_graph_series,
    find_historical_gap_matches,
)
from app.scanner.cash_future_history_store import find_expiry_close, read_history, save_history_point
from app.scanner.backtest_jobs import (
    cancel_job,
    create_full_fno_job,
    create_job,
    get_job,
    get_result_chunks,
    result_chunk_count,
)

router = APIRouter(prefix="/api/v1/scanner", tags=["Scanner"])


@router.get("/cash-future/evaluate")
def cash_future_scanner(
    symbol: str = Query(...), contract_month: str = Query("current"), cash_ltp: float = Query(...),
    future_ltp: float = Query(...), lot_size: int = Query(...), margin_required: float = Query(...),
    volume: int = Query(0, ge=0), oi: int = Query(0, ge=0), charges: float = Query(0.0, ge=0),
    funding_cost: float = Query(0.0, ge=0), min_gap: float = Query(0.0), min_gap_pct: float = Query(0.0),
    min_net_profit: float = Query(0.0), min_roi_pct: float = Query(0.0),
):
    try:
        config = CashFutureConfig(min_gap=min_gap, min_gap_pct=min_gap_pct, min_net_profit=min_net_profit,
                                  min_roi_pct=min_roi_pct, charges=charges, funding_cost=funding_cost)
        result = calculate_cash_future(CashQuote(symbol=symbol, ltp=cash_ltp),
            FutureQuote(symbol=symbol, contract_month=contract_month, ltp=future_ltp, lot_size=lot_size,
                        margin_required=margin_required, volume=volume, oi=oi), config)
        return {"status": "success", "scanner": "cash-future", "result": result.__dict__}
    except Exception as exc:
        app_logger.error(f"Cash-future scanner error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/cash-future/live")
def cash_future_live_scanner(symbols: str = Query(...), db: Session = Depends(get_db)):
    requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="at least one symbol is required")
    if len(requested) > 50:
        raise HTTPException(status_code=400, detail="maximum 50 symbols per live scan")
    result = CashFutureHistoryCollector(requested).collect(db)
    return {"status": "success", "scanner": "cash-future", "mode": "live",
            "count": len(result["collected"]), "data": result["collected"], "errors": result["errors"]}


@router.post("/cash-future/history")
def save_cash_future_history(symbol: str, contract_month: str, timestamp: datetime, cash_price: float,
    future_price: float, lot_size: int, margin_required: float, charges: float = 0.0,
    funding_cost: float = 0.0, volume: float | None = None, oi: float | None = None,
    expiry_date: date | None = None, db: Session = Depends(get_db)):
    if cash_price <= 0 or future_price <= 0 or lot_size <= 0 or margin_required < 0:
        raise HTTPException(status_code=400, detail="invalid Cash-Future history values")
    gap = future_price - cash_price
    gap_pct = gap / cash_price * 100.0
    gross = gap * lot_size
    net = gross - charges - funding_cost
    deployed = cash_price * lot_size + margin_required
    roi = net / deployed * 100.0 if deployed else 0.0
    point = CashFutureHistoryPoint(timestamp, symbol.upper(), contract_month, cash_price, future_price, gap, gap_pct,
                                   lot_size, margin_required, charges, funding_cost, net, roi, expiry_date)
    row = save_history_point(db, point, expiry_date=expiry_date)
    row.volume = volume
    row.oi = oi
    db.commit()
    return {"status": "success", "id": row.id, "timestamp": row.timestamp.isoformat(), "gap": row.gap, "gap_pct": row.gap_pct}


@router.post("/cash-future/history/collect")
def collect_cash_future_history(symbols: str = Query(...), db: Session = Depends(get_db)):
    requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="at least one symbol is required")
    if len(requested) > 50:
        raise HTTPException(status_code=400, detail="maximum 50 symbols per collection cycle")
    result = CashFutureHistoryCollector(requested).collect(db)
    return {"status": "success", "scanner": "cash-future", **result}


@router.get("/cash-future/history")
def get_cash_future_history(symbol: str, contract_month: str, days: int = Query(30, ge=1, le=3650), db: Session = Depends(get_db)):
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    return {"status": "success", "contract_month": contract_month, "count": len(points), "data": [p.__dict__ for p in points]}


@router.get("/cash-future/history/query")
def query_cash_future_gap(symbol: str, contract_month: str, target_gap: float, tolerance: float = Query(0.0, ge=0),
    days: int = Query(365, ge=1, le=3650), exit_gap: float = Query(0.0), max_holding_days: int = Query(30, ge=1, le=3650),
    charges_per_trade: float = Query(0.0, ge=0), funding_cost_per_trade: float = Query(0.0, ge=0), db: Session = Depends(get_db)):
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    matches = find_historical_gap_matches(points, target_gap, tolerance, contract_month)
    outcomes = analyze_historical_gap_outcomes(points, target_gap=target_gap, tolerance=tolerance,
        contract_month=contract_month, exit_gap=exit_gap, max_holding_days=max_holding_days,
        charges_per_trade=charges_per_trade, funding_cost_per_trade=funding_cost_per_trade)
    return {"status": "success", "target_gap": target_gap, "tolerance": tolerance, "contract_month": contract_month,
            "count": len(matches), "matches": [m.__dict__ for m in matches],
            "outcomes": [o.__dict__ | {"match": o.match.__dict__} for o in outcomes]}


@router.get("/cash-future/graph")
def cash_future_graph(symbol: str, contract_month: str, days: int = Query(30, ge=1, le=3650), db: Session = Depends(get_db)):
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    return {"status": "success", "scanner": "cash-future", "series": build_graph_series(points, contract_month)}


@router.get("/cash-future/backtest")
def cash_future_backtest(symbol: str, contract_month: str = Query(...), days: int = Query(365, ge=1, le=3650),
    min_entry_gap: float = Query(0.0), exit_gap: float = Query(0.0), charges_per_trade: float = Query(0.0, ge=0),
    funding_cost_per_trade: float = Query(0.0, ge=0), max_holding_days: int = Query(30, ge=1, le=3650), db: Session = Depends(get_db)):
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    if not points:
        raise HTTPException(status_code=404, detail="no historical Cash-Future observations found for this contract")
    result = run_backtest(points, BacktestConfig(min_entry_gap=min_entry_gap, exit_gap=exit_gap,
        charges_per_trade=charges_per_trade, funding_cost_per_trade=funding_cost_per_trade,
        max_holding_days=max_holding_days))
    return {"status": "success", "scanner": "cash-future", "contract_month": contract_month,
            "history_days": days, "backtest": result}


@router.post("/cash-future/backtest/jobs")
def start_cash_future_backtest_job(symbol: str = Query(...), contract_month: str = Query(...), days: int = Query(365, ge=1, le=3650),
    min_entry_gap: float = Query(0.0), exit_gap: float = Query(0.0), charges_per_trade: float = Query(0.0, ge=0),
    funding_cost_per_trade: float = Query(0.0, ge=0), max_holding_days: int = Query(30, ge=1, le=3650)):
    return {"status": "accepted", "job": create_job(symbol=symbol, contract_month=contract_month, days=days,
        min_entry_gap=min_entry_gap, exit_gap=exit_gap, charges_per_trade=charges_per_trade,
        funding_cost_per_trade=funding_cost_per_trade, max_holding_days=max_holding_days).job_id}


@router.post("/cash-future/backtest/full/jobs")
def start_full_fno_backtest_job(days: int = Query(365, ge=1, le=3650), min_entry_gap: float = Query(0.0),
    exit_gap: float = Query(0.0), charges_per_trade: float = Query(0.0, ge=0),
    funding_cost_per_trade: float = Query(0.0, ge=0), max_holding_days: int = Query(30, ge=1, le=3650),
    future_selection: str = Query("BOTH", pattern="^(CURRENT|NEAR|BOTH)$")):
    """Start full eligible stock-F&O backtest as a background job; UI remains non-blocking."""
    job = create_full_fno_job(days=days, min_entry_gap=min_entry_gap, exit_gap=exit_gap,
                              charges_per_trade=charges_per_trade, funding_cost_per_trade=funding_cost_per_trade,
                              max_holding_days=max_holding_days, future_selection=future_selection)
    return {"status": "accepted", "universe": "FULL_FNO_STOCK", "future_selection": future_selection,
            "job": job.job_id}


@router.get("/cash-future/backtest/jobs/{job_id}")
def cash_future_backtest_job_status(job_id: str, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="backtest job not found")
    chunk_count = result_chunk_count(db, job_id)
    return {"status": "success", "job": {"job_id": job.job_id, "status": job.status, "symbol": job.symbol,
        "contract_month": job.contract_month, "requested_days": job.requested_days, "progress_pct": job.progress_pct,
        "symbols_processed": job.symbols_processed, "symbols_total": job.symbols_total, "result_chunks": chunk_count,
        "message": job.message, "result": json.loads(job.result_json) if job.result_json else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None}}


@router.get("/cash-future/backtest/jobs/{job_id}/results")
def cash_future_backtest_job_results(job_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                                     db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="backtest job not found")
    chunks = get_result_chunks(db, job_id, offset=offset, limit=limit)
    return {"status": "success", "job_id": job_id, "offset": offset, "limit": limit,
            "total": result_chunk_count(db, job_id),
            "data": [{"sequence": c.sequence, "symbol": c.symbol, "result": json.loads(c.result_json),
                      "created_at": c.created_at.isoformat() if c.created_at else None} for c in chunks]}


@router.delete("/cash-future/backtest/jobs/{job_id}")
def cancel_cash_future_backtest_job(job_id: str, db: Session = Depends(get_db)):
    if not cancel_job(db, job_id):
        raise HTTPException(status_code=404, detail="backtest job not found or already finished")
    return {"status": "success", "job_id": job_id, "job_status": "cancelled"}


@router.get("/cash-future/expiry-close")
def cash_future_expiry_close(symbol: str, contract_month: str, expiry_date: date, db: Session = Depends(get_db)):
    result = find_expiry_close(db, symbol, contract_month, expiry_date)
    if result is None:
        raise HTTPException(status_code=404, detail="no 15:20-15:30 expiry-day observation found")
    return {"status": "success", "scanner": "cash-future", "expiry_close": result}