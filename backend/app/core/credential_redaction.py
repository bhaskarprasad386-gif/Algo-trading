"""Shared credential redaction for broker SDK and deployment diagnostics."""
from __future__ import annotations

import re

_SENSITIVE_KEYS = (
    r"x[-_]?private[-_]?key|x[-_]?api[-_]?key|api[-_]?key|private[-_]?key|"
    r"x[-_]?feed[-_]?token|feed[-_]?token|jwt[-_]?token|access[-_]?token|"
    r"refresh[-_]?token|auth[-_]?token|password|passwd|pwd|"
    r"totp(?:[-_]?secret)?|one[-_]?time[-_]?password|otp|"
    r"client[-_]?secret|client[-_]?code|client[-_]?id|api[-_]?secret"
)
_QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEYS}|authorization)['\"]?\s*[:=]\s*['\"])"
    rf"(?P<value>[^'\"\r\n]*)(?P<suffix>['\"])",
    re.IGNORECASE,
)
_PLAIN_ASSIGNMENT = re.compile(
    rf"(?P<prefix>\b(?:{_SENSITIVE_KEYS}|authorization)\b\s*[:=]\s*)"
    rf"(?P<value>Bearer\s+[^,\s;}}\]]+|[^,\s;}}\]]+)",
    re.IGNORECASE,
)


def redact_sensitive_text(message: str) -> str:
    """Redact credential-like key/value pairs while preserving diagnostic context."""
    redacted = _QUOTED_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
        str(message),
    )
    return _PLAIN_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        redacted,
    )
