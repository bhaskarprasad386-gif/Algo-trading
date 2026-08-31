from fastapi import FastAPI

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
from app.market_data.routes import router as market_data_router


Base.metadata.create_all(bind=engine)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
)


app.add_exception_handler(
    TradingAppException,
    trading_exception_handler,
)

app.add_exception_handler(
    Exception,
    global_exception_handler,
)

# यह लाइन सुनिश्चित करती है कि राउट्स ऐप में रजिस्टर हो जाएं
app.include_router(market_data_router)


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
