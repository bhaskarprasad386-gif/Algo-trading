from fastapi import APIRouter, Query

from app.market_data.client import MarketDataClient
from app.market_data.historical import HistoricalDataClient
from app.core.logger import app_logger


router = APIRouter(
    tags=["Market Data"],
)

# ✅ Lazy initialization - client create होंगे जब actually needed हो
_market_client = None
_historical_client = None


def get_market_client():
    """Get or create market data client."""
    global _market_client
    if _market_client is None:
        _market_client = MarketDataClient()
    return _market_client


def get_historical_client():
    """Get or create historical data client."""
    global _historical_client
    if _historical_client is None:
        _historical_client = HistoricalDataClient(get_market_client())
    return _historical_client


@router.get("/ltp")
def get_ltp(
    exchange: str = Query(...),
    tradingsymbol: str = Query(...),
    symboltoken: str = Query(...),
):
    """Get latest traded price for an instrument."""

    app_logger.info(
        f"LTP request: {exchange} {tradingsymbol} {symboltoken}"
    )

    return get_market_client().ltp(
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

    app_logger.info(
        f"Historical request: {exchange} {symboltoken} {interval}"
    )

    return get_historical_client().get_candles(
        exchange=exchange,
        symboltoken=symboltoken,
        interval=interval,
        from_date=from_date,
        to_date=to_date,
    )
