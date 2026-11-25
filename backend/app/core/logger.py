import logging
import sys

from logzero import logger as smartapi_logzero_logger
from loguru import logger

from app.core.credential_redaction import redact_sensitive_text


class SensitiveCredentialLogFilter(logging.Filter):
    """Redact SmartAPI SDK credentials before records reach any log handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # Fail closed: a formatting error must never emit the original args.
            record.msg = "[REDACTED: SDK log message formatting failed]"
            record.args = ()
            return True
        redacted = redact_sensitive_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_sensitive_credential_filter = SensitiveCredentialLogFilter()
for _sdk_logger in (
    smartapi_logzero_logger,
    logging.getLogger("SmartApi.smartConnect"),
):
    if not any(
        isinstance(item, SensitiveCredentialLogFilter)
        for item in _sdk_logger.filters
    ):
        _sdk_logger.addFilter(_sensitive_credential_filter)


logger.remove()

logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="DEBUG"
)

logger.add("backend_logs.log", rotation="10 MB", retention="10 days", level="INFO")

app_logger = logger
