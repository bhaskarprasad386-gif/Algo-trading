"""Production composition of live synthetic scanning and alert persistence."""

from __future__ import annotations

from time import monotonic
from typing import Callable

from app.scanner.live_synthetic_scanner import LiveSyntheticScanner
from app.notifications.synthetic_alerts import SyntheticAlertService


class LiveSyntheticScanPipeline:
    """Feeds recorder observations into scanner and durable alert delivery.

    The pipeline never places broker orders. Storage/notification failures are
    isolated from the market-data callback so a slow alert provider cannot stop
    the scanner.
    """

    def __init__(
        self,
        *,
        scanner: LiveSyntheticScanner,
        session_factory=None,
        alerts: SyntheticAlertService | None = None,
        on_results: Callable[[tuple], None] | None = None,
        retention_interval_seconds: float = 60.0,
    ) -> None:
        self.scanner = scanner
        self.session_factory = session_factory
        self.alerts = alerts or SyntheticAlertService()
        if retention_interval_seconds <= 0:
            raise ValueError("retention_interval_seconds must be positive")
        self.on_results = on_results
        self.retention_interval_seconds = retention_interval_seconds
        self._last_retention_cleanup = 0.0

    def observe(self, payload: dict) -> tuple:
        results = self.scanner.observe(payload)
        if not results:
            self._maybe_cleanup_retention()
            return ()
        if self.on_results is not None:
            try:
                self.on_results(results)
            except Exception:
                pass
        if self.session_factory is None:
            return results
        try:
            with self.session_factory() as db:
                self.alerts.persist(db, results)
                self._last_retention_cleanup = monotonic()
                self.alerts.notify_users(db, results)
        except Exception:
            pass
        return results

    
    def _maybe_cleanup_retention(self) -> None:
        now = monotonic()
        if now - self._last_retention_cleanup < self.retention_interval_seconds:
            return
        if self.session_factory is None:
            return
        try:
            with self.session_factory() as db:
                self.alerts.persist(db, ())
                self._last_retention_cleanup = now
        except Exception:
            pass


__all__ = ["LiveSyntheticScanPipeline"]
