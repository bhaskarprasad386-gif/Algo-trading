from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel
import asyncio
import queue
import threading
import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logger import app_logger
from app.core.exceptions import TradingAppException, trading_exception_handler, global_exception_handler
from app.core.database import engine, Base, SessionLocal
from app.models import User, Instrument, Order, Session, Position, SystemLog
from app.auth.routes import router as auth_router
from app.algo.auth import AngelOneAuth
from app.market_data.websocket import MarketDataWebSocket
from app.market_data.instruments import InstrumentMaster
from app.instruments.routes import router as instruments_router
from app.strategy_engine.routes import router as arbitrage_router
from app.order_engine.routes import router as orders_router
from app.market_data.routes import router as market_data_router
from app.scanner.routes import router as scanner_router
from app.scanner.auto_routes import router as auto_scanner_router
from app.execution.paper_routes import router as paper_execution_router
from app.scanner.cash_future_collector import CashFutureHistoryCollector

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.debug)
app.add_exception_handler(TradingAppException, trading_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

app.include_router(auth_router)
app.include_router(orders_router)
app.include_router(arbitrage_router)
app.include_router(instruments_router)
app.include_router(market_data_router)
app.include_router(scanner_router)
app.include_router(auto_scanner_router)
app.include_router(paper_execution_router)

DASHBOARD_FILE = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "index.html"


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(DASHBOARD_FILE, media_type="text/html")


@app.websocket("/ws/market-data/{symbol}")
async def market_data_websocket(websocket: WebSocket, symbol: str):
    await websocket.accept()
    client = MarketDataWebSocket()
    try:
        instrument = InstrumentMaster().get_instrument(symbol.strip().upper(), "NSE")
        if not instrument:
            await websocket.send_json({"status": "error", "detail": f"Instrument not found: NSE {symbol.strip().upper()}"})
            return
        messages = asyncio.Queue()

        def on_data(message):
            try:
                asyncio.get_running_loop().call_soon_threadsafe(messages.put_nowait, message)
            except RuntimeError:
                pass

        client.connect(exchange_type=1, tokens=[str(instrument["token"])], on_data=on_data)
        while True:
            message = await messages.get()
            await websocket.send_json(message if isinstance(message, dict) else {"data": message})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        app_logger.error(f"Market-data WebSocket error for {symbol}: {exc}")
        try:
            await websocket.send_json({"status": "error", "detail": str(exc)})
        except Exception:
            pass
    finally:
        client.close()

_history_collector_task: asyncio.Task | None = None
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def _collector_enabled() -> bool:
    return settings.CASH_FUTURE_HISTORY_ENABLED


def _collector_symbols() -> list[str]:
    return [item.strip().upper() for item in settings.CASH_FUTURE_HISTORY_SYMBOLS.split(",") if item.strip()]


def _collector_interval() -> int:
    return max(15, settings.CASH_FUTURE_HISTORY_INTERVAL_SECONDS)


def _market_is_open(now: datetime) -> bool:
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


async def _cash_future_history_loop() -> None:
    symbols = _collector_symbols()
    if not symbols:
        app_logger.warning("Cash-Future history collector enabled but no symbols configured")
        return
    interval = _collector_interval()
    collector = CashFutureHistoryCollector(symbols)
    app_logger.info(f"Cash-Future history collector started: {len(symbols)} symbols, {interval}s interval")
    while True:
        try:
            now_ist = datetime.now(IST)
            if _market_is_open(now_ist):
                db = SessionLocal()
                try:
                    result = await asyncio.to_thread(collector.collect, db)
                    app_logger.info(
                        f"Cash-Future history cycle complete: {len(result['collected'])} observations, "
                        f"{len(result['errors'])} errors"
                    )
                finally:
                    db.close()
            else:
                app_logger.debug("Cash-Future history collector sleeping outside NSE market hours")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.error(f"Cash-Future history cycle failed: {exc}")
        await asyncio.sleep(interval)


@app.on_event("startup")
async def startup_event():
    global _history_collector_task
    app_logger.info(f"{settings.app_name} started successfully in {settings.environment} mode")
    if _collector_enabled() and _history_collector_task is None:
        _history_collector_task = asyncio.create_task(_cash_future_history_loop())


@app.on_event("shutdown")
async def shutdown_event():
    global _history_collector_task
    if _history_collector_task is not None:
        _history_collector_task.cancel()
        try:
            await _history_collector_task
        except asyncio.CancelledError:
            pass
        _history_collector_task = None


@app.get("/")
def root():
    return {"message": "Algo Trading Platform is running", "environment": settings.environment, "version": "0.1.0"}


@app.get("/health")
def health_check():
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0", "database": "Connected"}
