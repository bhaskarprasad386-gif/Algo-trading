from fastapi import Request, status
from fastapi.responses import JSONResponse
from app.core.logger import app_logger

class TradingAppException(Exception):
    def __init__(self, name: str, message: str, status_code: int = 400):
        self.name = name
        self.message = message
        self.status_code = status_code

async def trading_exception_handler(request: Request, exc: TradingAppException):
    app_logger.error(f"Trading Exception [{exc.name}]: {exc.message} at path: {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.name,
            "message": exc.message
        }
    )

async def global_exception_handler(request: Request, exc: Exception):
    app_logger.critical(f"Unhandled Server Error: {str(exc)} at path: {request.url.path}")
    return JSONResponse(
        status_code=status_code.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "InternalServerError",
            "message": "An unexpected error occurred on the server."
        }
    )
