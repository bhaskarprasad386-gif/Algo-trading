from fastapi import APIRouter, Query, HTTPException
from app.core.logger import app_logger

router = APIRouter(
    tags=["Market Data"],
)


@router.get("/ltp")
def get_ltp(
    exchange: str = Query(...),
    tradingsymbol: str = Query(...),
    symboltoken: str = Query(...),
):
    from app.market_data.client import MarketDataClient

    try:
        app_logger.info(
            f"LTP request: {exchange} {tradingsymbol} {symboltoken}"
        )

        return MarketDataClient().ltp(
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            symboltoken=symboltoken,
        )

    except Exception as e:
        app_logger.error(f"LTP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/historical")
def get_historical(
    exchange: str = Query(...),
    symboltoken: str = Query(...),
    interval: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
):
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient

    try:
        app_logger.info(
            f"Historical request: {exchange} {symboltoken} {interval}"
        )

        market_client = MarketDataClient()
        historical_client = HistoricalDataClient(market_client)

        return historical_client.get_candles(
            exchange=exchange,
            symboltoken=symboltoken,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )

    except Exception as e:
        app_logger.error(f"Historical error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
