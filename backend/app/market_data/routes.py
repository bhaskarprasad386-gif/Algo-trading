from fastapi import APIRouter, Query
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
    
    # ✅ Import यहाँ करो - जब actually call हो
    from app.market_data.client import MarketDataClient
    
    app_logger.info(
        f"LTP request: {exchange} {tradingsymbol} {symboltoken}"
    )
    
    market_client = MarketDataClient()
    return market_client.ltp(
        exchange=exchange,
        tradingsymbol=tradingsymbol,
        symboltoken=symboltoken,
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
    
    # ✅ Import यहाँ करो - जब actually call हो
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient
    
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
