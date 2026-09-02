from fastapi import APIRouter, HTTPException, Query

from app.core.logger import app_logger
from app.market_data.historical import HistoricalDataClient
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
