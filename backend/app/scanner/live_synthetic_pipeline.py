"""Production composition of live synthetic scanning and alert persistence."""

from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Lock, Thread
from time import monotonic, sleep
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
        # A second bounded queue absorbs short bursts without blocking feed callbacks.
        self._overflow_queue = Queue(maxsize=alert_queue_size)
        self._state_lock = Lock()
        self._close_lock = Lock()
        self._closed = False
        self._stop_enqueued = False
        self._counters = {
            "alert_jobs_enqueued": 0,
            "alert_jobs_completed": 0,
            "alert_jobs_dropped": 0,
            "alert_job_errors": 0,
            "alert_job_retries": 0,
            "alert_jobs_failed": 0,
        }
        self._max_job_attempts = 3
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
                try:
                    self._overflow_queue.put_nowait(results)
                except Full:
                    self._counters["alert_jobs_dropped"] += 1
                else:
                    self._counters["alert_jobs_enqueued"] += 1
            else:
                self._counters["alert_jobs_enqueued"] += 1

    def _process_alert_job(self, job) -> None:
        succeeded = False
        for attempt in range(self._max_job_attempts):
            try:
                # Use a fresh DB session on each attempt; a failed SQL
                # transaction may have left the previous session unusable.
                with self.session_factory() as db:
                    if job is None:
                        self.alerts.persist(db, ())
                    else:
                        self.alerts.persist(db, job)
                        self.alerts.notify_users(db, job)
                if job is None:
                    self._last_retention_cleanup = monotonic()
                succeeded = True
                with self._state_lock:
                    self._counters["alert_jobs_completed"] += 1
                break
            except Exception:
                with self._state_lock:
                    self._counters["alert_job_errors"] += 1
                    if attempt + 1 < self._max_job_attempts:
                        self._counters["alert_job_retries"] += 1
                if attempt + 1 < self._max_job_attempts:
                    sleep(0.05 * (2 ** attempt))
        if not succeeded:
            with self._state_lock:
                self._counters["alert_jobs_failed"] += 1

    def _run_alert_worker(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=0.05)
                source_queue = self._queue
            except Empty:
                try:
                    job = self._overflow_queue.get_nowait()
                    source_queue = self._overflow_queue
                except Empty:
                    continue
            if job is self._STOP:
                # Drain spillover before stopping. Since close() has already
                # marked the pipeline closed, no producer can add new work.
                try:
                    overflow_job = self._overflow_queue.get_nowait()
                except Empty:
                    source_queue.task_done()
                    return
                self._queue.put_nowait(self._STOP)
                source_queue.task_done()
                try:
                    self._process_alert_job(overflow_job)
                finally:
                    self._overflow_queue.task_done()
                continue
            try:
                self._process_alert_job(job)
            finally:
                source_queue.task_done()

    def flush(self, timeout: float | None = None) -> bool:
        """Wait for queued work to finish; timeout is best-effort."""
        if self._worker is None:
            return True
        if timeout is None:
            self._queue.join()
            return True
        deadline = monotonic() + max(0.0, timeout)
        while (self._queue.unfinished_tasks or self._overflow_queue.unfinished_tasks) and monotonic() < deadline:
            # Event-based waiting without holding up the market-data callback.
            from threading import Event
            Event().wait(min(0.01, max(0.0, deadline - monotonic())))
        return self._queue.unfinished_tasks == 0 and self._overflow_queue.unfinished_tasks == 0

    def close(self, timeout: float = 2.0) -> bool:
        """Stop accepting alert jobs and drain the worker within a time budget.

        Never hold the counter/state lock while waiting for queue capacity:
        the worker needs that lock to finish its current job before it can
        dequeue the next item. Holding it here deadlocks a full queue.
        """
        if self._worker is None:
            return True
        timeout = max(0.0, timeout)
        deadline = monotonic() + timeout
        with self._close_lock:
            with self._state_lock:
                self._closed = True
                stop_enqueued = self._stop_enqueued
            if not stop_enqueued:
                try:
                    self._queue.put(self._STOP, timeout=max(0.0, deadline - monotonic()))
                except Full:
                    # Keep rejecting new work; a later close() can retry.
                    return False
                with self._state_lock:
                    self._stop_enqueued = True
            self._worker.join(timeout=max(0.0, deadline - monotonic()))
            return not self._worker.is_alive()

    def diagnostics_snapshot(self) -> dict:
        with self._state_lock:
            counters = dict(self._counters)
            closed = self._closed
        return {
            "alert_worker_enabled": self._worker is not None,
            "alert_worker_alive": bool(self._worker and self._worker.is_alive()),
            "alert_queue_depth": self._queue.qsize() + self._overflow_queue.qsize(),
            "alert_queue_capacity": self._queue.maxsize + self._overflow_queue.maxsize,
            "alert_queue_primary_depth": self._queue.qsize(),
            "alert_queue_overflow_depth": self._overflow_queue.qsize(),
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
                try:
                    self._overflow_queue.put_nowait(None)
                except Full:
                    # Retention cleanup is best-effort; never count it as a lost alert.
                    return
            self._last_retention_cleanup = now
            self._counters["alert_jobs_enqueued"] += 1


__all__ = ["LiveSyntheticScanPipeline"]
