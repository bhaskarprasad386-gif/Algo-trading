"""Process-local, provider-neutral broker session registry.

Authenticated adapters remain in memory only. A lease pins an adapter while a
request uses it. Revocation removes it from lookup first, then waits for active
leases to drain before disconnecting, preventing disconnect/use races.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import Condition, RLock
from typing import Iterator

from app.brokers.base import BrokerAdapter


class BrokerSessionStore:
    """Thread-safe adapter store with request-scoped session leases."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[int, str], BrokerAdapter] = {}
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._active_leases: dict[int, int] = {}

    @staticmethod
    def _key(owner_id: int, broker: str) -> tuple[int, str]:
        normalized = str(broker).strip().lower()
        if not normalized:
            raise ValueError("broker name cannot be empty")
        return int(owner_id), normalized

    def _wait_until_idle(self, adapter: BrokerAdapter) -> None:
        adapter_id = id(adapter)
        with self._condition:
            while self._active_leases.get(adapter_id, 0):
                self._condition.wait()

    @contextmanager
    def lease(self, owner_id: int, broker: str) -> Iterator[BrokerAdapter | None]:
        """Pin the current adapter for the full duration of a request operation.

        A session removed/replaced after acquisition may finish this in-flight
        operation, but cannot be acquired by a new request. Revocation waits for
        this context to exit before disconnecting the provider client.
        """
        key = self._key(owner_id, broker)
        with self._condition:
            adapter = self._sessions.get(key)
            if adapter is not None:
                adapter_id = id(adapter)
                self._active_leases[adapter_id] = self._active_leases.get(adapter_id, 0) + 1
        try:
            yield adapter
        finally:
            if adapter is not None:
                with self._condition:
                    adapter_id = id(adapter)
                    remaining = self._active_leases.get(adapter_id, 0) - 1
                    if remaining > 0:
                        self._active_leases[adapter_id] = remaining
                    else:
                        self._active_leases.pop(adapter_id, None)
                    self._condition.notify_all()

    def replace(
        self, owner_id: int, broker: str, adapter: BrokerAdapter
    ) -> BrokerAdapter | None:
        key = self._key(owner_id, broker)
        adapter_name = str(getattr(adapter, "name", "")).strip().lower()
        if adapter_name != key[1]:
            raise ValueError("adapter name does not match broker session key")
        with self._lock:
            previous = self._sessions.get(key)
            self._sessions[key] = adapter
            return previous

    def replace_and_disconnect_previous(
        self, owner_id: int, broker: str, adapter: BrokerAdapter
    ) -> Exception | None:
        """Publish new session, drain old leases, then best-effort disconnect old."""
        previous = self.replace(owner_id, broker, adapter)
        if previous is None:
            return None
        self._wait_until_idle(previous)
        try:
            previous.disconnect()
        except Exception as exc:
            return exc
        return None

    def get(self, owner_id: int, broker: str) -> BrokerAdapter | None:
        """Compatibility lookup only; request handlers must use lease()."""
        key = self._key(owner_id, broker)
        with self._lock:
            return self._sessions.get(key)

    def remove(self, owner_id: int, broker: str) -> BrokerAdapter | None:
        key = self._key(owner_id, broker)
        with self._lock:
            adapter = self._sessions.pop(key, None)
        if adapter is not None:
            self._wait_until_idle(adapter)
        return adapter

    def clear_owner(self, owner_id: int) -> list[BrokerAdapter]:
        owner = int(owner_id)
        with self._lock:
            keys = [key for key in self._sessions if key[0] == owner]
            sessions = [self._sessions.pop(key) for key in keys]
        for adapter in sessions:
            self._wait_until_idle(adapter)
        return sessions

    def clear(self) -> list[BrokerAdapter]:
        """Remove all sessions, then wait until no request holds a lease."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for adapter in sessions:
            self._wait_until_idle(adapter)
        return sessions

    def disconnect_all(self) -> list[tuple[str, Exception]]:
        """Drain all leases before best-effort provider logout."""
        failures: list[tuple[str, Exception]] = []
        for adapter in self.clear():
            name = str(getattr(adapter, "name", "unknown"))
            try:
                adapter.disconnect()
            except Exception as exc:
                failures.append((name, exc))
        return failures


broker_sessions = BrokerSessionStore()
