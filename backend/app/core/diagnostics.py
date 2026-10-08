from __future__ import annotations

from collections import deque
from datetime import datetime
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class RuntimeDiagnostics:
    """Small in-memory ring buffer for actionable runtime errors."""

    def __init__(self, maxlen: int = 100) -> None:
        self._lock = Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def record(
        self,
        *,
        component: str,
        severity: str = "ERROR",
        message: str,
        error_type: str | None = None,
        endpoint: str | None = None,
        status_code: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(IST)
        event = {
            "id": f"{now.timestamp():.6f}",
            "time": now.isoformat(),
            "component": str(component),
            "severity": str(severity).upper(),
            "message": str(message),
            "error_type": error_type,
            "endpoint": endpoint,
            "status_code": status_code,
            "context": context or {},
        }
        with self._lock:
            self._events.appendleft(event)

    def snapshot(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[: max(1, min(int(limit), len(self._events)))]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


runtime_diagnostics = RuntimeDiagnostics()
