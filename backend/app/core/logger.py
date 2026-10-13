import sys
from loguru import logger

# SmartApi's SDK uses logzero and can emit authentication request details.
# Disable that SDK logger globally; application diagnostics continue through Loguru.
try:
    import logzero

    logzero.logger.disabled = True
except Exception:
    # Logging hardening must never prevent application startup.
    pass

logger.remove()

logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="DEBUG"
)

logger.add("backend_logs.log", rotation="10 MB", retention="10 days", level="INFO")

app_logger = logger
