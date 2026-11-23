"""Process-local, provider-neutral broker session registry.

The registry owns only authenticated adapter objects in memory. It never stores
credentials, access tokens, or broker responses, and it does not imply that a
session can be restored after a process restart.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable

from app.brokers.base import BrokerAdapter


class BrokerSessionStore:
    """Thread-safe in-memory adapter store keyed by owner and broker."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[int, str], BrokerAdapter] = {}
        self._lock = RLock()

    @staticmethod
    def _key(owner_id: int, broker: str) -> tuple[int, str]:
        normalized = str(broker).strip().lower()
        if not normalized:
            raise ValueError("broker name cannot be empty")
        return int(owner_id), normalized

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
        """Install the new adapter and best-effort close the previous provider session.

        The new adapter becomes the only locally reachable session before the
        old provider logout is attempted. A remote logout failure is returned
        to the caller for logging, but never rolls back the new local session.
        """
        previous = self.replace(owner_id, broker, adapter)
        if previous is None:
            return None
        try:
            previous.disconnect()
        except Exception as exc:
            return exc
        return None

    def commit_if_current(
        self, owner_id: int, broker: str, adapter: BrokerAdapter, operation: Callable[[], None]
    ) -> bool:
        """Run a metadata commit only while this adapter remains the current session."""
        key = self._key(owner_id, broker)
        with self._lock:
            if self._sessions.get(key) is not adapter:
                return False
            operation()
            return True

    def remove_if_current(
        self, owner_id: int, broker: str, adapter: BrokerAdapter
    ) -> bool:
        """Remove a session only if the registry still owns this exact adapter."""
        key = self._key(owner_id, broker)
        with self._lock:
            if self._sessions.get(key) is not adapter:
                return False
            self._sessions.pop(key, None)
            return True


    def get(self, owner_id: int, broker: str) -> BrokerAdapter | None:
        key = self._key(owner_id, broker)
        with self._lock:
            return self._sessions.get(key)

    def remove(self, owner_id: int, broker: str) -> BrokerAdapter | None:
        key = self._key(owner_id, broker)
        with self._lock:
            return self._sessions.pop(key, None)

    def clear_owner(self, owner_id: int) -> list[BrokerAdapter]:
        owner = int(owner_id)
        with self._lock:
            keys = [key for key in self._sessions if key[0] == owner]
            return [self._sessions.pop(key) for key in keys]

    def clear(self) -> list[BrokerAdapter]:
        """Remove all in-memory sessions; intended for controlled shutdown/tests."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            return sessions

    def disconnect_all(self) -> list[tuple[str, Exception]]:
        """Atomically drain sessions and best-effort terminate every provider session.

        The store is cleared before network/provider cleanup starts, so callers
        cannot retrieve a session while shutdown is in progress. One provider's
        disconnect failure never prevents the remaining sessions from closing.
        """
        failures: list[tuple[str, Exception]] = []
        for adapter in self.clear():
            name = str(getattr(adapter, "name", "unknown"))
            try:
                adapter.disconnect()
            except Exception as exc:
                failures.append((name, exc))
        return failures


broker_sessions = BrokerSessionStore()
