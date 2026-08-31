from fastapi import FastAPI, Query, HTTPException
from app.instruments.routes import router as instruments_router

from app.core.config import settings
from app.core.logger import app_logger
from app.core.exceptions import (
    TradingAppException,
    trading_exception_handler,
    global_exception_handler,
)
from app.core.database import engine, Base
from app.models import user, instrument, order
from app.algo.auth import AngelOneAuth


Base.metadata.create_all(bind=engine)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
)
app.include_router(instruments_router)


app.add_exception_handler(
    TradingAppException,
    trading_exception_handler,
)

app.add_exception_handler(
    Exception,
    global_exception_handler,
)


@app.on_event("startup")
async def startup_event():
    app_logger.info(
        f"{settings.app_name} database & base skeleton initialized "
        f"successfully in {settings.environment} mode"
    )


@app.get("/")
def root():
    return {
        "message": "Algo Trading Platform & Milestone 1 Foundation is running",
        "environment": settings.environment,
        "version": "0.1.0",
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0",
        "database": "Connected & Schema Created",
    }


@app.post("/api/v1/login")
def login_angel_one():
    """Login to Angel One using configured credentials."""

    app_logger.info(
        "Initiating Angel One login process via API"
    )

    auth = AngelOneAuth()

    return auth.login()


@app.get("/api/v1/market-data/ltp")
def get_ltp(
    exchange: str = Query(...),
    tradingsymbol: str = Query(...),
    symboltoken: str = Query(...),
):
    """Get latest traded price for an instrument."""
    from app.market_data.client import MarketDataClient
    try:
        app_logger.info(f"LTP request: {exchange} {tradingsymbol} {symboltoken}")
        return MarketDataClient().ltp(
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            symboltoken=symboltoken,
        )
    except Exception as e:
        app_logger.error(f"LTP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/market-data/historical")
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
    try:
        app_logger.info(f"Historical request: {exchange} {symboltoken} {interval}")
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
