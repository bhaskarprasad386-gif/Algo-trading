import logging
import re
import sys
from logzero import logger as smartapi_logzero_logger
from loguru import logger


class SensitiveCredentialLogFilter(logging.Filter):
    """Redact credentials from SmartAPI SDK messages before they reach log handlers."""

    _sensitive_keys = (
        r"x[-_]?private[-_]?key|x[-_]?api[-_]?key|api[-_]?key|private[-_]?key|"
        r"x[-_]?feed[-_]?token|feed[-_]?token|jwt[-_]?token|access[-_]?token|"
        r"refresh[-_]?token|auth[-_]?token|password|passwd|pwd|totp(?:[-_]?secret)?|"
        r"client[-_]?secret|client[-_]?code"
    )
    _quoted_key_value = re.compile(
        rf"(?P<prefix>['\"]?(?:{_sensitive_keys}|authorization)['\"]?\s*[:=]\s*['\"])(?P<value>[^'\"]*)(?P<suffix>['\"])",
        re.IGNORECASE,
    )
    _plain_key_value = re.compile(
        rf"(?P<prefix>\b(?:{_sensitive_keys}|authorization)\s*[:=]\s*)(?P<value>Bearer\s+[^\s,;]+|[^\s,;]+)",
        re.IGNORECASE,
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = self._quoted_key_value.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
            message,
        )
        redacted = self._plain_key_value.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]",
            redacted,
        )
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_sensitive_credential_filter = SensitiveCredentialLogFilter()
for _sdk_logger in (smartapi_logzero_logger, logging.getLogger("SmartApi.smartConnect")):
    if not any(isinstance(item, SensitiveCredentialLogFilter) for item in _sdk_logger.filters):
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
