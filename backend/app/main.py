from fastapi import FastAPI
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug
)


@app.get("/")
def root():
    return {
        "message": "Algo Trading Platform is running",
        "environment": settings.environment
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0"
    }
