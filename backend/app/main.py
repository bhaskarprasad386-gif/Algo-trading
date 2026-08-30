from fastapi import FastAPI
from app.core.config import settings
import logging

# Basic logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug
)

@app.on_event("startup")
async def startup_event():
    logger.info(f"{settings.app_name} started")
    logger.info(f"Environment: {settings.environment}")

@app.get("/")
def root():
    return {
        "message": "Algo Trading Platform is running",
        "environment": settings.environment,
        "version": "0.1.0"
    }

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0"
    }
