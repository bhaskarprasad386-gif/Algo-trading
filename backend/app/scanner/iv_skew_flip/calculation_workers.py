"""Bounded spawn-based worker primitive for IV-only CPU calculations.

The worker owns no broker clients, database sessions, feed adapters or runtime
objects. Callers send immutable/picklable snapshots and consume results
non-blockingly. One in-flight job and one replaceable pending job are allowed.
"""
from __future__ import annotations

import importlib
import multiprocessing as mp
from queue import Empty, Full
from threading import RLock
from time import monotonic
from typing import Any


def _resolve_handler(path: str):
    module_name, separator, attribute = path.rpartition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("handler must use 'module:function' notation")
    handler = getattr(importlib.import_module(module_name), attribute)
    if not callable(handler):
        raise TypeError("worker handler is not callable")
    return handler


def _worker_main(handler_path: str, jobs: Any, results: Any) -> None:
    """Spawn target: deliberately imports only a pure calculation handler."""
    try:
        handler = _resolve_handler(handler_path)
    except BaseException as exc:
        try:
            results.put_nowait({
                "kind": "worker_init_error",
                "error": type(exc).__name__,
            })
        except Full:
            pass
        return

    while True:
        job = jobs.get()
        if job is None:
            return
        job_id = job.get("job_id")
        try:
            value = handler(job.get("payload"))
            message = {
                "kind": "result",
                "job_id": job_id,
                "value": value,
            }
        except BaseException as exc:
            message = {
                "kind": "error",
                "job_id": job_id,
                "error": type(exc).__name__,
            }
        # The parent drains results on every refresh. Never let a slow parent
        # block the worker forever behind an obsolete result.
        try:
            results.put_nowait(message)
        except Full:
            try:
                results.get_nowait()
            except Empty:
                pass
            try:
                results.put_nowait(message)
            except Full:
                pass


class BoundedProcessWorker:
    """One isolated spawn process with bounded latest-value admission.

    submit() never waits for calculation completion. poll() never waits for a
    child. A timed-out worker is terminated and restarted without touching its
    parent process or any market-data owner.
    """

    def __init__(
        self,
        *,
        name: str,
        handler_path: str,
        timeout_seconds: float = 5.0,
        restart_limit: int = 3,
        restart_window_seconds: float = 60.0,
        context: Any | None = None,
    ) -> None:
        if not name or not handler_path:
            raise ValueError("worker name and handler path are required")
        if timeout_seconds <= 0 or restart_limit < 0 or restart_window_seconds <= 0:
            raise ValueError("invalid worker timeout or restart budget")
        self.name = name
        self.handler_path = handler_path
        self.timeout_seconds = float(timeout_seconds)
        self.restart_limit = int(restart_limit)
        self.restart_window_seconds = float(restart_window_seconds)
        self._ctx = context or mp.get_context("spawn")
        self._lock = RLock()
        self._process = None
        self._jobs = None
        self._results = None
        self._inflight: dict[str, Any] | None = None
        self._restart_times: list[float] = []
        self._next_job_id = 0
        self._coalesced = 0
        self._timeouts = 0
        self._failures = 0
        self._restarts = 0
        self._last_error: str | None = None
        self._stopped = True

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.is_alive():
                return self.snapshot()
            self._spawn_locked()
            self._stopped = False
            return self.snapshot()

    def _spawn_locked(self) -> None:
        self._jobs = self._ctx.Queue(maxsize=1)
        self._results = self._ctx.Queue(maxsize=1)
        self._process = self._ctx.Process(
            target=_worker_main,
            args=(self.handler_path, self._jobs, self._results),
            name=f"iv-calc-{self.name}",
            daemon=True,
        )
        self._process.start()

    @staticmethod
    def _drain(queue: Any) -> int:
        drained = 0
        while True:
            try:
                queue.get_nowait()
                drained += 1
            except Empty:
                return drained

    def submit(self, payload: Any, *, now: float | None = None) -> dict[str, Any]:
        """Admit a new immutable payload, replacing only queued stale work."""
        stamp = monotonic() if now is None else float(now)
        with self._lock:
            if self._stopped:
                raise RuntimeError("worker is stopped")
            if self._process is None or not self._process.is_alive():
                self._recover_locked(stamp)
            self._next_job_id += 1
            job_id = f"{self.name}:{self._next_job_id}"
            job = {"job_id": job_id, "payload": payload}
            try:
                self._jobs.put_nowait(job)
            except Full:
                self._drain(self._jobs)
                self._coalesced += 1
                try:
                    self._jobs.put_nowait(job)
                except Full:
                    self._failures += 1
                    self._last_error = "QUEUE_ADMISSION_FAILED"
                    return {"accepted": False, "job_id": job_id, "reason": self._last_error}
            # Deadline includes time spent waiting behind an in-flight job.
            # If it expires, restart discards both the stuck calculation and
            # the stale pending snapshot; the next refresh can submit anew.
            self._inflight = {"job_id": job_id, "started_at": stamp}
            return {"accepted": True, "job_id": job_id}

    def poll(self, *, now: float | None = None) -> dict[str, Any] | None:
        """Return one completed result or None; never blocks on the child."""
        stamp = monotonic() if now is None else float(now)
        with self._lock:
            if self._stopped:
                return None
            if self._process is None or not self._process.is_alive():
                self._recover_locked(stamp)
                return {"kind": "worker_restarted", "worker": self.name}
            try:
                message = self._results.get_nowait()
            except Empty:
                message = None
            if message is not None:
                if self._inflight is not None and message.get("job_id") == self._inflight.get("job_id"):
                    self._inflight = None
                if message.get("kind") == "result":
                    return message
                self._failures += 1
                self._last_error = str(message.get("error") or message.get("kind"))
                return message
            if self._inflight is not None and stamp - self._inflight["started_at"] > self.timeout_seconds:
                expired = dict(self._inflight)
                self._timeouts += 1
                self._last_error = "CALCULATION_TIMEOUT"
                self._inflight = None
                self._recover_locked(stamp, force=True)
                return {
                    "kind": "timeout",
                    "job_id": expired["job_id"],
                    "worker": self.name,
                }
            return None

    def mark_dispatched(self, job_id: str, *, now: float | None = None) -> None:
        """Mark the most recently admitted job as in-flight for deadline checks."""
        stamp = monotonic() if now is None else float(now)
        with self._lock:
            self._inflight = {"job_id": str(job_id), "started_at": stamp}

    def _recover_locked(self, now: float, *, force: bool = False) -> None:
        process = self._process
        if process is not None and process.is_alive():
            if not force:
                return
            process.terminate()
            process.join(timeout=1.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1.0)
        self._process = None
        for queue in (self._jobs, self._results):
            if queue is not None:
                try:
                    queue.close()
                    queue.join_thread()
                except Exception:
                    pass
        self._jobs = self._results = None
        self._restart_times = [
            item for item in self._restart_times
            if now - item <= self.restart_window_seconds
        ]
        if len(self._restart_times) >= self.restart_limit:
            self._last_error = "RESTART_BUDGET_EXHAUSTED"
            self._stopped = True
            return
        self._restart_times.append(now)
        self._restarts += 1
        self._spawn_locked()
        self._inflight = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            return {
                "name": self.name,
                "status": (
                    "STOPPED" if self._stopped else
                    "RUNNING" if process is not None and process.is_alive() else
                    "DEGRADED"
                ),
                "pid": process.pid if process is not None else None,
                "queue_capacity": 1,
                "inflight": self._inflight is not None,
                "coalesced_jobs": self._coalesced,
                "timeouts": self._timeouts,
                "failures": self._failures,
                "restarts": self._restarts,
                "restart_budget_remaining": max(0, self.restart_limit - len(self._restart_times)),
                "last_error": self._last_error,
            }

    def stop(self, *, timeout_seconds: float = 2.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        with self._lock:
            self._stopped = True
            process = self._process
            jobs = self._jobs
            if process is None:
                return True
            if process.is_alive() and jobs is not None:
                try:
                    jobs.put_nowait(None)
                except Full:
                    self._drain(jobs)
                    try:
                        jobs.put_nowait(None)
                    except Full:
                        pass
            process.join(timeout=timeout_seconds)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1.0)
            stopped = not process.is_alive()
            self._process = None
            self._inflight = None
            for queue in (self._jobs, self._results):
                if queue is not None:
                    try:
                        queue.close()
                        queue.join_thread()
                    except Exception:
                        pass
            self._jobs = self._results = None
            return stopped


__all__ = ["BoundedProcessWorker"]
