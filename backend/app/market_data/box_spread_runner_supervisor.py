"""Make-before-break supervisor for Box Spread live runner sessions.

The supervisor owns candidate worker threads and transfers the shared publisher
only after the candidate reports complete staged readiness. Failed staging leaves
the current runner untouched.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterable


class BoxSpreadRunnerSupervisor:
    def __init__(
        self,
        *,
        active_runner,
        active_thread,
        runner_factory: Callable[[Iterable], object],
        on_active: Callable[[object], None] | None = None,
        readiness_timeout_seconds: float = 60.0,
        readiness_poll_seconds: float = 0.25,
    ):
        if active_runner is None or active_thread is None:
            raise ValueError("an active Box Spread runner and worker thread are required")
        if readiness_timeout_seconds <= 0 or readiness_poll_seconds <= 0:
            raise ValueError("readiness timeouts must be positive")
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._active_runner = active_runner
        self._active_thread = active_thread
        self._runner_factory = runner_factory
        self._on_active = on_active
        self._readiness_timeout = float(readiness_timeout_seconds)
        self._readiness_poll = float(readiness_poll_seconds)
        self._candidate_runner = None
        self._candidate_thread = None
        self._old_threads = {id(active_runner): active_thread}
        self._handoff_thread = None
        self._last_handoff = {"state": "IDLE"}

    @staticmethod
    def _start_runner(runner, name):
        errors = []

        def target():
            try:
                runner.run_forever()
            except BaseException as exc:
                errors.append(exc)
                runner.stop()

        thread = threading.Thread(target=target, name=name, daemon=True)
        try:
            setattr(runner, "_runner_worker", thread)
        except Exception:
            pass
        thread.start()
        return thread, errors

    def request_handoff(self, targets, *, on_failed=None):
        targets = tuple(targets)
        if not targets:
            raise ValueError("Box Spread handoff requires at least one target")
        with self._lock:
            if self._shutdown.is_set():
                return {"state": "SHUTTING_DOWN"}
            if self._handoff_thread is not None and self._handoff_thread.is_alive():
                return {"state": "HANDOFF_ALREADY_RUNNING"}
            old_runner = self._active_runner
            candidate = self._runner_factory(targets)
            if candidate is old_runner:
                raise ValueError("staged Box Spread runner must be a distinct instance")
            if getattr(candidate, "_publish_enabled", True):
                candidate.deactivate_publishing()
            self._candidate_runner = candidate
            self._last_handoff = {
                "state": "STAGING",
                "target_count": len(targets),
                "symbols": sorted({str(t.underlying).strip().upper() for t in targets}),
            }
            candidate_thread, errors = self._start_runner(candidate, "box-spread-staged-runner")
            self._candidate_thread = candidate_thread
            handoff_thread = threading.Thread(
                target=self._complete_handoff,
                args=(old_runner, candidate, candidate_thread, errors, on_failed),
                name="box-spread-handoff-supervisor",
                daemon=True,
            )
            self._handoff_thread = handoff_thread
            handoff_thread.start()
            return dict(self._last_handoff)

    def _complete_handoff(self, old_runner, candidate, candidate_thread, errors, on_failed):
        deadline = time.monotonic() + self._readiness_timeout
        readiness = {"ready": False, "state": "WAITING_FOR_READINESS"}
        while not self._shutdown.is_set() and time.monotonic() < deadline:
            if not candidate_thread.is_alive() or candidate.stop_event.is_set():
                break
            if errors:
                break
            try:
                readiness = candidate.staged_readiness()
            except Exception as exc:
                readiness = {"ready": False, "state": "READINESS_ERROR", "reason": str(exc)[:240]}
            if readiness.get("ready") is True:
                # activate_publishing delegates to the shared gate. Its lock
                # waits for any in-flight old publisher and transfers ownership
                # atomically before old runner shutdown begins.
                try:
                    candidate.activate_publishing()
                except Exception as exc:
                    # activate_publishing may have acquired the shared gate
                    # before a later pipeline toggle failed. Explicitly revoke
                    # the candidate and restore the old owner before aborting.
                    try:
                        candidate.deactivate_publishing()
                    finally:
                        try:
                            old_runner.activate_publishing()
                        except Exception:
                            pass
                    readiness = {
                        "ready": False,
                        "state": "PUBLISHER_TRANSFER_FAILED",
                        "reason": str(exc)[:240],
                    }
                    break
                if not candidate_thread.is_alive() or candidate.stop_event.is_set():
                    candidate.deactivate_publishing()
                    old_runner.activate_publishing()
                    break
                with self._lock:
                    if self._shutdown.is_set():
                        candidate.deactivate_publishing()
                        old_runner.activate_publishing()
                        break
                    old_thread = self._old_threads.get(id(old_runner))
                    self._old_threads[id(candidate)] = candidate_thread
                    self._active_runner = candidate
                    self._active_thread = candidate_thread
                    self._candidate_runner = None
                    self._candidate_thread = None
                    self._last_handoff = {
                        "state": "ACTIVE",
                        "readiness": readiness,
                        "target_count": len(candidate.targets),
                        "symbols": sorted({t.underlying for t in candidate.targets}),
                    }
                if self._on_active is not None:
                    try:
                        self._on_active(candidate)
                    except Exception:
                        # Ownership has transferred; callback failure must not
                        # interrupt deterministic cleanup of the old runner.
                        pass
                old_runner.stop()
                if old_runner is not candidate:
                    self._join_worker(old_thread, timeout=5.0)
                return
            self._shutdown.wait(self._readiness_poll)

        candidate.stop()
        self._join_worker(candidate_thread, timeout=5.0)
        with self._lock:
            self._candidate_runner = None
            self._candidate_thread = None
            state = "CANCELLED" if self._shutdown.is_set() else "FAILED"
            self._last_handoff = {
                "state": state,
                "readiness": readiness,
                "error": str(errors[0])[:240] if errors else None,
            }
        if state == "FAILED" and on_failed is not None:
            try:
                on_failed()
            except Exception:
                # Preserve the live runner even if durable selection rollback fails.
                pass

    def attach_active_thread(self, runner, thread):
        """Register the original worker before enabling handoffs."""
        with self._lock:
            if runner is not self._active_runner:
                raise ValueError("runner is not the active Box Spread owner")
            self._old_threads[id(runner)] = thread

    def _join_worker(self, thread, timeout):
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=timeout)

    @property
    def active_runner(self):
        with self._lock:
            return self._active_runner

    @property
    def active_thread(self):
        with self._lock:
            return self._active_thread

    @property
    def is_shutting_down(self):
        return self._shutdown.is_set()

    def snapshot(self):
        with self._lock:
            return {
                "active_symbols": sorted({t.underlying for t in self._active_runner.targets}),
                "candidate_running": bool(self._candidate_thread and self._candidate_thread.is_alive()),
                "handoff_in_progress": bool(self._handoff_thread and self._handoff_thread.is_alive()),
                "handoff": dict(self._last_handoff),
            }

    def stop(self):
        self._shutdown.set()
        with self._lock:
            runners = (self._candidate_runner, self._active_runner)
            threads = (self._candidate_thread, self._active_thread, self._handoff_thread)
        for runner in runners:
            if runner is not None:
                runner.stop()
        for thread in threads:
            self._join_worker(thread, timeout=5.0)


__all__ = ["BoxSpreadRunnerSupervisor"]
