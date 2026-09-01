from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import asyncio
import random
import uuid

from app.core.config import settings
from app.core.logger import app_logger
from app.core.exceptions import (
    TradingAppException,
    trading_exception_handler,
    global_exception_handler,
)
from app.core.database import engine, Base
from app.models import User, Instrument, Order, Session, Position, SystemLog
from app.algo.auth import AngelOneAuth

# Routers
from app.instruments.routes import router as instruments_router
from app.strategy_engine.routes import router as arbitrage_router
from app.order_engine.routes import router as orders_router
from app.market_data.routes import router as market_data_router

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
)

# Exception Handlers
app.add_exception_handler(TradingAppException, trading_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# Include Routers
app.include_router(orders_router)
app.include_router(arbitrage_router)
app.include_router(instruments_router)
app.include_router(market_data_router)


@app.on_event("startup")
async def startup_event():
    app_logger.info(
        f"{settings.app_name} started successfully in {settings.environment} mode"
    )


@app.get("/")
def root():
    return {
        "message": "Algo Trading Platform is running",
        "environment": settings.environment,
        "version": "0.1.0",
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0",
        "database": "Connected",
    }


@app.post("/api/v1/auth/login")
def login_angel_one():
    """Login to Angel One."""
    app_logger.info("Angel One login requested")
    return AngelOneAuth().login()


@app.get("/api/v1/auth/status")
def auth_status():
    """Check Angel One session status."""
    return AngelOneAuth().status()


@app.post("/api/v1/auth/refresh")
def auth_refresh():
    """Refresh Angel One session."""
    return AngelOneAuth().refresh_session()


@app.post("/api/v1/auth/logout")
def auth_logout():
    """Clear Angel One session."""
    return AngelOneAuth().logout()


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
    """Legacy paper-order endpoint retained for compatibility."""
    if not order.symbol.strip():
        raise ValueError("symbol is required")
    if order.quantity <= 0:
        raise ValueError("quantity must be greater than zero")
    if order.transactionType.upper() not in {"BUY", "SELL"}:
        raise ValueError("transactionType must be BUY or SELL")

    generated_order_id = str(uuid.uuid4())[:8].upper()
    return {
        "orderId": generated_order_id,
        "status": "SUCCESS",
        "message": (
            f"Successfully simulated {order.transactionType.upper()} order "
            f"for {order.quantity} shares of {order.symbol.upper()}"
        ),
    }


@app.websocket("/ws/market-data/{symbol}")
async def websocket_market_data(websocket: WebSocket, symbol: str):
    """Temporary mock WebSocket retained until the public streaming adapter is wired."""
    await websocket.accept()
    app_logger.info(f"WebSocket connected for symbol: {symbol}")

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
                "spread": 1.00,
            }

            await websocket.send_json(data)
            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        app_logger.info(f"WebSocket disconnected for symbol: {symbol}")
