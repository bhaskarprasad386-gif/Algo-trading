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
    """Get latest traded price for an instrument."""

    from app.market_data.client import MarketDataClient

    app_logger.info(
        f"LTP request: {exchange} {tradingsymbol} {symboltoken}"
    )

    try:
        market_client = MarketDataClient()

        return market_client.ltp(
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            symboltoken=symboltoken,
        )

    except Exception as e:
        app_logger.error(
            f"LTP error: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.get("/historical")
def get_historical(
    exchange: str = Query(...),
    symboltoken: str = Query(...),
    interval: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
):
    """Get historical candle data."""

    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient

    app_logger.info(
        f"Historical request: {exchange} {symboltoken} {interval}"
    )

    try:
        market_client = MarketDataClient()

        historical_client = HistoricalDataClient(
            market_client
        )

        return historical_client.get_candles(
            exchange=exchange,
            symboltoken=symboltoken,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )

    except Exception as e:
        app_logger.error(
            f"Historical error: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail=str(e),
        )
