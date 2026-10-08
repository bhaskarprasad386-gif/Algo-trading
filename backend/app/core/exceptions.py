from fastapi import Request, status
from fastapi.responses import JSONResponse
from app.core.logger import app_logger
from app.core.diagnostics import runtime_diagnostics


class TradingAppException(Exception):
    def __init__(self, name: str, message: str, status_code: int = 400):
        self.name = name
        self.message = message
        self.status_code = status_code


async def trading_exception_handler(request: Request, exc: TradingAppException):
    runtime_diagnostics.record(component="Backend/API", error_type=type(exc).__name__, message=exc.message, endpoint=request.url.path, status_code=exc.status_code)
    app_logger.error(
        f"Trading Exception [{exc.name}]: {exc.message} at path: {request.url.path}"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.name,
            "message": exc.message,
        },
    )


async def global_exception_handler(request: Request, exc: Exception):
    runtime_diagnostics.record(component="Backend/API", severity="CRITICAL", error_type=type(exc).__name__, message=str(exc), endpoint=request.url.path, status_code=500)
    app_logger.critical(
        f"Unhandled Server Error: {str(exc)} at path: {request.url.path}"
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "InternalServerError",
            "message": "An unexpected error occurred on the server.",
        },
    )
