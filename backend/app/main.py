from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import asyncio
import queue
import threading
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
from app.market_data.websocket import MarketDataWebSocket
from app.market_data.instruments import InstrumentMaster

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
    """Stream real Angel One market ticks for an NSE equity symbol."""
    await websocket.accept()

    symbol = symbol.strip().upper()
    if not symbol:
        await websocket.close(code=1008, reason="symbol is required")
        return

    master = InstrumentMaster()
    token = master.get_token(symbol, "NSE")
    if not token:
        await websocket.close(code=1008, reason=f"NSE symbol not found: {symbol}")
        return

    tick_queue: queue.Queue = queue.Queue(maxsize=100)
    client = MarketDataWebSocket()

    def on_data(message):
        try:
            if tick_queue.full():
                try:
                    tick_queue.get_nowait()
                except queue.Empty:
                    pass
            tick_queue.put_nowait(message)
        except Exception as exc:
            app_logger.error(f"Failed to queue market tick: {exc}")

    def run_client():
        try:
            client.connect(
                exchange_type=1,
                tokens=[token],
                mode=1,
                correlation_id=f"market-{symbol}-{uuid.uuid4().hex[:8]}",
                on_data=on_data,
            )
        except Exception as exc:
            app_logger.error(f"Market WebSocket client stopped: {exc}")

    worker = threading.Thread(
        target=run_client,
        name=f"angel-ws-{symbol}",
        daemon=True,
    )
    worker.start()
    app_logger.info(f"Public market WebSocket connected for {symbol}")

    try:
        while True:
            try:
                tick = await asyncio.to_thread(tick_queue.get, True, 30)
                await websocket.send_json(tick)
            except queue.Empty:
                await websocket.send_json({"status": "connected", "symbol": symbol})
    except WebSocketDisconnect:
        app_logger.info(f"Public market WebSocket disconnected for {symbol}")
    finally:
        client.close()
