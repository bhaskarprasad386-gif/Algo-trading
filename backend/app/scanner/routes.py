from datetime import date, datetime, timedelta

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

router = APIRouter(prefix="/api/v1/scanner", tags=["Scanner"])


@router.get("/cash-future/evaluate")
def cash_future_scanner(
    symbol: str = Query(...),
    contract_month: str = Query("current"),
    cash_ltp: float = Query(..., gt=0),
    future_ltp: float = Query(..., gt=0),
    lot_size: int = Query(..., gt=0),
    margin_required: float = Query(..., ge=0),
    volume: int = Query(0, ge=0),
    oi: int = Query(0, ge=0),
    charges: float = Query(0.0, ge=0),
    funding_cost: float = Query(0.0, ge=0),
    min_gap: float = Query(0.0),
    min_gap_pct: float = Query(0.0),
    min_net_profit: float = Query(0.0),
    min_roi_pct: float = Query(0.0),
):
    """Evaluate one cash/current-or-near-future pair using customizable filters."""
    try:
        config = CashFutureConfig(min_gap=min_gap, min_gap_pct=min_gap_pct, min_net_profit=min_net_profit,
                                  min_roi_pct=min_roi_pct, charges=charges, funding_cost=funding_cost)
        result = calculate_cash_future(
            CashQuote(symbol=symbol, ltp=cash_ltp),
            FutureQuote(symbol=symbol, contract_month=contract_month, ltp=future_ltp,
                        lot_size=lot_size, margin_required=margin_required, volume=volume, oi=oi),
            config,
        )
        return {"status": "success", "scanner": "cash-future", "result": result.__dict__}
    except Exception as exc:
        app_logger.error(f"Cash-future scanner error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/cash-future/live")
def cash_future_live_scanner(
    symbols: str = Query(..., description="Comma-separated NSE cash symbols, e.g. RELIANCE,SBIN"),
    db: Session = Depends(get_db),
):
    """Fetch live NSE cash + nearest two NFO futures and return scanner observations."""
    requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="at least one symbol is required")
    if len(requested) > 50:
        raise HTTPException(status_code=400, detail="maximum 50 symbols per live scan")

    result = CashFutureHistoryCollector(requested).collect(db)
    return {
        "status": "success",
        "scanner": "cash-future",
        "mode": "live",
        "count": len(result["collected"]),
        "data": result["collected"],
        "errors": result["errors"],
    }


@router.post("/cash-future/history")
def save_cash_future_history(
    symbol: str,
    contract_month: str,
    timestamp: datetime,
    cash_price: float,
    future_price: float,
    lot_size: int,
    margin_required: float,
    charges: float = 0.0,
    funding_cost: float = 0.0,
    volume: float | None = None,
    oi: float | None = None,
    expiry_date: date | None = None,
    db: Session = Depends(get_db),
):
    """Persist one live observation; repeated timestamp updates the same row."""
    if cash_price <= 0 or future_price <= 0 or lot_size <= 0 or margin_required < 0:
        raise HTTPException(status_code=400, detail="invalid Cash-Future history values")
    gap = future_price - cash_price
    gap_pct = gap / cash_price * 100.0
    gross = gap * lot_size
    net = gross - charges - funding_cost
    deployed = cash_price * lot_size + margin_required
    roi = net / deployed * 100.0 if deployed else 0.0
    point = CashFutureHistoryPoint(timestamp, symbol.upper(), contract_month, cash_price, future_price,
                                   gap, gap_pct, lot_size, margin_required, charges, funding_cost, net, roi,
                                   expiry_date)
    row = save_history_point(db, point, expiry_date=expiry_date)
    row.volume = volume
    row.oi = oi
    db.commit()
    return {"status": "success", "id": row.id, "timestamp": row.timestamp.isoformat(), "gap": row.gap, "gap_pct": row.gap_pct}


@router.post("/cash-future/history/collect")
def collect_cash_future_history(
    symbols: str = Query(..., description="Comma-separated NSE cash symbols, e.g. RELIANCE,SBIN"),
    db: Session = Depends(get_db),
):
    """Collect and persist one CURRENT and one NEAR observation for each symbol."""
    requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="at least one symbol is required")
    if len(requested) > 50:
        raise HTTPException(status_code=400, detail="maximum 50 symbols per collection cycle")
    result = CashFutureHistoryCollector(requested).collect(db)
    return {"status": "success", "scanner": "cash-future", **result}


@router.get("/cash-future/history")
def get_cash_future_history(
    symbol: str,
    contract_month: str,
    days: int = Query(30, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    return {"status": "success", "contract_month": contract_month, "count": len(points), "data": [p.__dict__ for p in points]}


@router.get("/cash-future/history/query")
def query_cash_future_gap(
    symbol: str,
    contract_month: str,
    target_gap: float,
    tolerance: float = Query(0.0, ge=0),
    days: int = Query(365, ge=1, le=3650),
    exit_gap: float = Query(0.0),
    max_holding_days: int = Query(30, ge=1, le=3650),
    charges_per_trade: float = Query(0.0, ge=0),
    funding_cost_per_trade: float = Query(0.0, ge=0),
    db: Session = Depends(get_db),
):
    """Find prior same/greater gaps and report subsequent convergence outcomes."""
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    matches = find_historical_gap_matches(points, target_gap, tolerance, contract_month)
    outcomes = analyze_historical_gap_outcomes(
        points,
        target_gap=target_gap,
        tolerance=tolerance,
        contract_month=contract_month,
        exit_gap=exit_gap,
        max_holding_days=max_holding_days,
        charges_per_trade=charges_per_trade,
        funding_cost_per_trade=funding_cost_per_trade,
    )
    return {
        "status": "success",
        "target_gap": target_gap,
        "tolerance": tolerance,
        "contract_month": contract_month,
        "count": len(matches),
        "matches": [m.__dict__ for m in matches],
        "outcomes": [o.__dict__ | {"match": o.match.__dict__} for o in outcomes],
    }


@router.get("/cash-future/graph")
def cash_future_graph(
    symbol: str,
    contract_month: str,
    days: int = Query(30, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    return {"status": "success", "contract_month": contract_month, "series": build_graph_series(points, contract_month)}


@router.get("/cash-future/backtest")
def cash_future_backtest(
    symbol: str,
    contract_month: str = Query(..., description="Use CURRENT or NEAR (or an exact contract identifier); contracts are never mixed."),
    days: int = Query(365, ge=1, le=3650),
    min_entry_gap: float = Query(0.0),
    exit_gap: float = Query(0.0),
    charges_per_trade: float = Query(0.0, ge=0),
    funding_cost_per_trade: float = Query(0.0, ge=0),
    max_holding_days: int = Query(30, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """Run an independent historical convergence backtest for one future contract series."""
    end = datetime.utcnow()
    points = read_history(db, symbol, contract_month, end - timedelta(days=days), end)
    if not points:
        raise HTTPException(status_code=404, detail="no historical Cash-Future observations found for this contract")
    result = run_backtest(
        points,
        BacktestConfig(
            min_entry_gap=min_entry_gap,
            exit_gap=exit_gap,
            charges_per_trade=charges_per_trade,
            funding_cost_per_trade=funding_cost_per_trade,
            max_holding_days=max_holding_days,
        ),
    )
    return {
        "status": "success",
        "scanner": "cash-future",
        "contract_month": contract_month,
        "history_days": days,
        "backtest": result,
    }


@router.get("/cash-future/expiry-close")
def cash_future_expiry_close(
    symbol: str,
    contract_month: str,
    expiry_date: date,
    db: Session = Depends(get_db),
):
    result = find_expiry_close(db, symbol, contract_month, expiry_date)
    if result is None:
        raise HTTPException(status_code=404, detail="no 15:20-15:30 expiry-day observation found")
    return {"status": "success", "scanner": "cash-future", "expiry_close": result}
