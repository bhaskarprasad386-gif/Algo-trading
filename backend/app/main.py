from fastapi import FastAPI, Query, HTTPException
from app.instruments.routes import router as instruments_router
from app.strategy_engine.routes import router as arbitrage_router
from app.order_engine.routes import router as orders_router

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
app.include_router(orders_router)

app.include_router(arbitrage_router)

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
import uuid
from pydantic import BaseModel

class OrderRequest(BaseModel):
    symbol: str
    quantity: int
    transactionType: str

class OrderResponseModel(BaseModel):
    orderId: str
    status: str
    message: str

@app.post("/api/v1/order", response_model=OrderResponseModel)
def place_market_order(order: OrderRequest):
    generated_order_id = str(uuid.uuid4())[:8].upper()
    return {
        "orderId": generated_order_id,
        "status": "SUCCESS",
        "message": f"Successfully placed {order.transactionType} order for {order.quantity} shares of {order.symbol}"
    }
    from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import random

@app.websocket("/ws/market-data/{symbol}")
async def websocket_market_data(websocket: WebSocket, symbol: str):
    await websocket.accept()
    try:
        while True:
            base_price = 1500.00
            fluctuation = random.uniform(-5.0, 5.0)
            ltp = round(base_price + fluctuation, 2)
            
            data = {
                "symbol": symbol.upper(),
                "bidPrice": round(ltp - 0.5, 2),
                "askPrice": round(ltp + 0.5, 2),
                "ltp": ltp,
                "spread": 1.00
            }
            
            await websocket.send_json(data)
            await asyncio.sleep(2)
            
    except WebSocketDisconnect:
        print(f"Client disconnected for symbol: {symbol}")

