from fastapi import APIRouter, HTTPException, Query

from app.core.logger import app_logger
from app.market_data.historical import HistoricalDataClient
from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future
from app.scanner.rsi_obv import scan

router = APIRouter(prefix="/api/v1/scanner", tags=["Scanner"])


@router.get("/rsi-obv")
def rsi_obv_scanner(
    exchange: str = Query(...),
    symboltoken: str = Query(...),
    interval: str = Query("ONE_DAY"),
    from_date: str = Query(...),
    to_date: str = Query(...),
):
    """Run the first scanner on historical candles for one instrument."""
    try:
        response = HistoricalDataClient().get_candles(
            exchange=exchange,
            symboltoken=symboltoken,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )
        candles = response.get("data") or []
        result = scan(candles)
        return {
            "status": "success",
            "scanner": "rsi-obv",
            "exchange": exchange.upper(),
            "symboltoken": symboltoken,
            "interval": interval,
            "result": result,
        }
    except Exception as exc:
        app_logger.error(f"RSI/OBV scanner error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))


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
        config = CashFutureConfig(
            min_gap=min_gap,
            min_gap_pct=min_gap_pct,
            min_net_profit=min_net_profit,
            min_roi_pct=min_roi_pct,
            charges=charges,
            funding_cost=funding_cost,
        )
        result = calculate_cash_future(
            CashQuote(symbol=symbol, ltp=cash_ltp),
            FutureQuote(
                symbol=symbol,
                contract_month=contract_month,
                ltp=future_ltp,
                lot_size=lot_size,
                margin_required=margin_required,
                volume=volume,
                oi=oi,
            ),
            config,
        )
        return {"status": "success", "scanner": "cash-future", "result": result.__dict__}
    except Exception as exc:
        app_logger.error(f"Cash-future scanner error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
