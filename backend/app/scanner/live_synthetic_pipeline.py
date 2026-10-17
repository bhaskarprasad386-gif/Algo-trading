"""Production composition of live synthetic scanning and alert persistence."""

from __future__ import annotations

from queue import Full, Queue
from threading import Lock, Thread
from time import monotonic
from typing import Callable

from app.scanner.live_synthetic_scanner import LiveSyntheticScanner
from app.notifications.synthetic_alerts import SyntheticAlertService


class LiveSyntheticScanPipeline:
    """Scan ticks synchronously; perform database and notification I/O off-thread.

    Alert work is bounded so a stalled database/provider cannot block websocket
    callbacks or grow memory without limit. Queue overflow and worker failures
    are exposed through diagnostics_snapshot().
    """

    _STOP = object()

    def __init__(
        self,
        *,
        scanner: LiveSyntheticScanner,
        session_factory=None,
        alerts: SyntheticAlertService | None = None,
        on_results: Callable[[tuple], None] | None = None,
        retention_interval_seconds: float = 60.0,
        alert_queue_size: int = 256,
    ) -> None:
        self.scanner = scanner
        self.session_factory = session_factory
        self.alerts = alerts or SyntheticAlertService()
        if retention_interval_seconds <= 0:
            raise ValueError("retention_interval_seconds must be positive")
        if alert_queue_size <= 0:
            raise ValueError("alert_queue_size must be positive")
        self.on_results = on_results
        self.retention_interval_seconds = retention_interval_seconds
        self._last_retention_cleanup = 0.0
        self._observe_lock = Lock()
        self._queue = Queue(maxsize=alert_queue_size)
        self._state_lock = Lock()
        self._closed = False
        self._counters = {
            "alert_jobs_enqueued": 0,
            "alert_jobs_completed": 0,
            "alert_jobs_dropped": 0,
            "alert_job_errors": 0,
        }
        self._worker = None
        if self.session_factory is not None:
            self._worker = Thread(
                target=self._run_alert_worker,
                name="synthetic-alert-worker",
                daemon=True,
            )
            self._worker.start()

    def observe(self, payload: dict) -> tuple:
        with self._observe_lock:
            before = self._scan_evaluations()
            results = self.scanner.observe(payload)
            after = self._scan_evaluations()
            calculation_completed = after > before or (
                before == 0 and after == 0 and bool(results)
            )
            if calculation_completed and self.on_results is not None:
                try:
                    self.on_results(tuple(results))
                except Exception:
                    pass

        if results:
            self._enqueue(tuple(results))
        else:
            self._maybe_cleanup_retention()
        return tuple(results)

    def _enqueue(self, results: tuple) -> None:
        if self._worker is None:
            return
        with self._state_lock:
            if self._closed:
                self._counters["alert_jobs_dropped"] += 1
                return
            try:
                self._queue.put_nowait(results)
            except Full:
                self._counters["alert_jobs_dropped"] += 1
            else:
                self._counters["alert_jobs_enqueued"] += 1

    def _run_alert_worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is self._STOP:
                    return
                try:
                    with self.session_factory() as db:
                        if job is None:
                            self.alerts.persist(db, ())
                        else:
                            self.alerts.persist(db, job)
                            self.alerts.notify_users(db, job)
                    if job is None:
                        self._last_retention_cleanup = monotonic()
                    with self._state_lock:
                        self._counters["alert_jobs_completed"] += 1
                except Exception:
                    with self._state_lock:
                        self._counters["alert_job_errors"] += 1
            finally:
                self._queue.task_done()

    def flush(self, timeout: float | None = None) -> bool:
        """Wait for queued work to finish; timeout is best-effort."""
        if self._worker is None:
            return True
        if timeout is None:
            self._queue.join()
            return True
        deadline = monotonic() + max(0.0, timeout)
        while self._queue.unfinished_tasks and monotonic() < deadline:
            # Event-based waiting without holding up the market-data callback.
            from threading import Event
            Event().wait(min(0.01, max(0.0, deadline - monotonic())))
        return self._queue.unfinished_tasks == 0

    def close(self, timeout: float = 2.0) -> bool:
        """Stop accepting alert jobs and drain the worker within a time budget."""
        if self._worker is None:
            return True
        with self._state_lock:
            if not self._closed:
                self._closed = True
                try:
                    self._queue.put(self._STOP, timeout=max(0.0, timeout))
                except Full:
                    # Keep the pipeline open so a later close() can retry after
                    # the worker frees queue capacity.
                    self._closed = False
                    return False
        self._worker.join(timeout=max(0.0, timeout))
        return not self._worker.is_alive()

    def diagnostics_snapshot(self) -> dict:
        with self._state_lock:
            counters = dict(self._counters)
            closed = self._closed
        return {
            "alert_worker_enabled": self._worker is not None,
            "alert_worker_alive": bool(self._worker and self._worker.is_alive()),
            "alert_queue_depth": self._queue.qsize(),
            "alert_queue_capacity": self._queue.maxsize,
            "alert_worker_closed": closed,
            "counters": counters,
        }

    def _scan_evaluations(self) -> int:
        try:
            snapshot = self.scanner.diagnostics_snapshot()
            return int(snapshot.get("counters", {}).get("scan_evaluations", 0))
        except (AttributeError, TypeError, ValueError):
            return 0

    def _maybe_cleanup_retention(self) -> None:
        now = monotonic()
        if now - self._last_retention_cleanup < self.retention_interval_seconds:
            return
        if self.session_factory is None or self._worker is None:
            return
        with self._state_lock:
            if self._closed:
                return
            try:
                self._queue.put_nowait(None)
            except Full:
                self._counters["alert_jobs_dropped"] += 1
            else:
                self._last_retention_cleanup = now
                self._counters["alert_jobs_enqueued"] += 1


__all__ = ["LiveSyntheticScanPipeline"]
