"""Lifecycle-safe strategy plug-in runtime.

The runtime owns strategy lifecycle state and dependency checks. Strategy code
receives a shared market feed; it never receives broker credentials or sockets.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Any, Callable

from .common_strategy_feed import CommonStrategyMarketFeed
from .strategy_manifest import StrategyManifest


class StrategyState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    FAILED = "failed"


@dataclass(frozen=True)
class StrategyRuntimeSnapshot:
    strategy_id: str
    state: StrategyState
    events: int
    errors: int
    last_error: str | None


class StrategyRuntime:
    """Isolate one strategy instance behind a bounded shared-feed lifecycle."""

    def __init__(
        self,
        manifest: StrategyManifest,
        *,
        feed: CommonStrategyMarketFeed | None = None,
    ) -> None:
        self.manifest = manifest
        self.feed = feed
        self._lock = RLock()
        self._state = StrategyState.STOPPED
        self._events = 0
        self._errors = 0
        self._last_error: str | None = None

    @property
    def state(self) -> StrategyState:
        with self._lock:
            return self._state

    def start(
        self,
        descriptors: list[Any] | tuple[Any, ...],
        on_record: Callable[[Any], None],
    ) -> tuple[Any, ...]:
        with self._lock:
            if self._state == StrategyState.RUNNING:
                raise RuntimeError(f"strategy already running: {self.manifest.normalized_id}")
            if not self.manifest.live_enabled:
                raise RuntimeError(f"live strategy is disabled: {self.manifest.normalized_id}")
            feed = self.feed or CommonStrategyMarketFeed(self.manifest.normalized_id)
            self.feed = feed
            self._errors = 0
            self._last_error = None

            def guarded(record: Any) -> None:
                try:
                    on_record(record)
                except Exception as exc:
                    with self._lock:
                        self._errors += 1
                        self._last_error = f"{type(exc).__name__}: {exc}"
                    return
                with self._lock:
                    self._events += 1

            try:
                keys = feed.start(descriptors, guarded)
            except Exception as exc:
                self._state = StrategyState.FAILED
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise
            self._state = StrategyState.RUNNING
            return keys

    def stop(self) -> None:
        with self._lock:
            if self.feed is not None:
                self.feed.stop()
            self._state = StrategyState.STOPPED

    def fail(self, error: BaseException) -> None:
        with self._lock:
            self._errors += 1
            self._last_error = f"{type(error).__name__}: {error}"
            self._state = StrategyState.FAILED

    def snapshot(self) -> StrategyRuntimeSnapshot:
        with self._lock:
            return StrategyRuntimeSnapshot(
                strategy_id=self.manifest.normalized_id,
                state=self._state,
                events=self._events,
                errors=self._errors,
                last_error=self._last_error,
            )


__all__ = ["StrategyRuntime", "StrategyRuntimeSnapshot", "StrategyState"]
