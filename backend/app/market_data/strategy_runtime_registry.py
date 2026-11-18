"""Final strategy runtime registry.

Provides one lifecycle owner per strategy id while keeping every strategy
isolated behind the shared market-data feed. The registry never owns broker
credentials or order clients.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from .strategy_runtime import StrategyRuntime, StrategyRuntimeSnapshot
from .strategy_manifest import StrategyManifest


class StrategyRuntimeRegistry:
    """Thread-safe registry for independent strategy runtime instances."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._runtimes: dict[str, StrategyRuntime] = {}

    def register(self, runtime: StrategyRuntime) -> StrategyRuntime:
        key = runtime.manifest.normalized_id
        with self._lock:
            existing = self._runtimes.get(key)
            if existing is not None and existing is not runtime:
                raise ValueError(f"strategy runtime already registered: {key}")
            self._runtimes[key] = runtime
            return runtime

    def create(
        self,
        manifest: StrategyManifest,
        *,
        feed: Any | None = None,
    ) -> StrategyRuntime:
        runtime = StrategyRuntime(manifest, feed=feed)
        return self.register(runtime)

    def get(self, strategy_id: str) -> StrategyRuntime | None:
        with self._lock:
            return self._runtimes.get(str(strategy_id).strip().lower())

    def start(
        self,
        strategy_id: str,
        descriptors: list[Any] | tuple[Any, ...],
        on_record: Callable[[Any], None],
    ) -> tuple[Any, ...]:
        runtime = self.get(strategy_id)
        if runtime is None:
            raise KeyError(f"strategy runtime not registered: {strategy_id}")
        return runtime.start(descriptors, on_record)

    def stop(self, strategy_id: str) -> None:
        runtime = self.get(strategy_id)
        if runtime is not None:
            runtime.stop()

    def stop_all(self) -> None:
        with self._lock:
            runtimes = tuple(self._runtimes.values())
        for runtime in runtimes:
            runtime.stop()

    def snapshots(self) -> tuple[StrategyRuntimeSnapshot, ...]:
        with self._lock:
            runtimes = tuple(self._runtimes.values())
        return tuple(sorted((runtime.snapshot() for runtime in runtimes),
                            key=lambda item: item.strategy_id))

    def clear_stopped(self) -> int:
        with self._lock:
            keys = [
                key for key, runtime in self._runtimes.items()
                if runtime.snapshot().state.value == "stopped"
            ]
            for key in keys:
                del self._runtimes[key]
            return len(keys)


__all__ = ["StrategyRuntimeRegistry"]
