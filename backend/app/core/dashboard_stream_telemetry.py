"""Bounded process-local telemetry for the browser dashboard publisher."""
from __future__ import annotations

from threading import Lock
from time import monotonic
from typing import Any


class DashboardStreamTelemetry:
    """Track aggregate publisher health without retaining clients or payloads."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cycles = 0
        self._publish_errors = 0
        self._client_send_successes = 0
        self._client_send_failures = 0
        self._last_error_type: str | None = None
        self._last_success_at: float | None = None
        self._last_build_seconds = 0.0
        self._last_build_dispatch_wait_seconds = 0.0
        self._max_build_dispatch_wait_seconds = 0.0
        self._last_serialization_seconds = 0.0
        self._max_serialization_seconds = 0.0
        self._last_payload_bytes = 0
        self._last_fanout_seconds = 0.0
        self._max_build_seconds = 0.0
        self._max_fanout_seconds = 0.0
        self._last_client_count = 0
        self._last_sent_count = 0
        self._last_failed_count = 0
        self._last_client_send_seconds = 0.0
        self._last_cycle_seconds = 0.0
        self._max_cycle_seconds = 0.0
        self._cycle_overrun_count = 0
        self._max_client_send_seconds = 0.0

    def record_client_send(self, *, success: bool, duration_seconds: float) -> None:
        duration = max(0.0, float(duration_seconds))
        with self._lock:
            if success:
                self._client_send_successes += 1
            else:
                self._client_send_failures += 1
            self._last_client_send_seconds = duration
            self._max_client_send_seconds = max(self._max_client_send_seconds, duration)

    def record_publish(
        self,
        *,
        client_count: int,
        sent_count: int,
        failed_count: int,
        build_seconds: float,
        fanout_seconds: float,
        build_dispatch_wait_seconds: float = 0.0,
        serialization_seconds: float = 0.0,
        payload_bytes: int = 0,
        cycle_seconds: float = 0.0,
        error_type: str | None = None,
    ) -> None:
        build = max(0.0, float(build_seconds))
        build_dispatch_wait = max(0.0, float(build_dispatch_wait_seconds))
        fanout = max(0.0, float(fanout_seconds))
        serialization = max(0.0, float(serialization_seconds))
        cycle = max(0.0, float(cycle_seconds))
        with self._lock:
            self._cycles += 1
            self._last_client_count = max(0, int(client_count))
            self._last_sent_count = max(0, int(sent_count))
            self._last_failed_count = max(0, int(failed_count))
            self._last_build_seconds = build
            self._last_build_dispatch_wait_seconds = build_dispatch_wait
            self._max_build_dispatch_wait_seconds = max(
                self._max_build_dispatch_wait_seconds, build_dispatch_wait
            )
            self._last_fanout_seconds = fanout
            self._last_serialization_seconds = serialization
            self._max_serialization_seconds = max(self._max_serialization_seconds, serialization)
            self._last_payload_bytes = max(0, int(payload_bytes))
            self._last_cycle_seconds = cycle
            self._max_cycle_seconds = max(self._max_cycle_seconds, cycle)
            if cycle > 1.0:
                self._cycle_overrun_count += 1
            self._max_build_seconds = max(self._max_build_seconds, build)
            self._max_fanout_seconds = max(self._max_fanout_seconds, fanout)
            if error_type:
                self._publish_errors += 1
                candidate = str(error_type).split(":", 1)[0].strip()
                self._last_error_type = candidate[:64] if candidate.isidentifier() else "UnknownError"
            else:
                self._last_success_at = monotonic()
                self._last_error_type = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            success_age = (
                None if self._last_success_at is None
                else max(0.0, monotonic() - self._last_success_at)
            )
            return {
                "cycles": self._cycles,
                "publish_errors": self._publish_errors,
                "client_send_successes": self._client_send_successes,
                "client_send_failures": self._client_send_failures,
                "last_error_type": self._last_error_type,
                "seconds_since_last_success": (
                    None if success_age is None else round(success_age, 3)
                ),
                "last_build_seconds": round(self._last_build_seconds, 4),
                "last_build_dispatch_wait_seconds": round(self._last_build_dispatch_wait_seconds, 4),
                "max_build_dispatch_wait_seconds": round(self._max_build_dispatch_wait_seconds, 4),
                "max_build_seconds": round(self._max_build_seconds, 4),
                "last_serialization_seconds": round(self._last_serialization_seconds, 4),
                "max_serialization_seconds": round(self._max_serialization_seconds, 4),
                "last_payload_bytes": self._last_payload_bytes,
                "last_fanout_seconds": round(self._last_fanout_seconds, 4),
                "max_fanout_seconds": round(self._max_fanout_seconds, 4),
                "last_client_count": self._last_client_count,
                "last_sent_count": self._last_sent_count,
                "last_failed_count": self._last_failed_count,
                "last_cycle_seconds": round(self._last_cycle_seconds, 4),
                "max_cycle_seconds": round(self._max_cycle_seconds, 4),
                "cycle_overrun_count": self._cycle_overrun_count,
                "last_client_send_seconds": round(self._last_client_send_seconds, 4),
                "max_client_send_seconds": round(self._max_client_send_seconds, 4),
            }
