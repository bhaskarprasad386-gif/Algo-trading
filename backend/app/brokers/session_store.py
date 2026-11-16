"""Process-local, provider-neutral broker session registry.

The registry owns only authenticated adapter objects in memory. It never stores
credentials, access tokens, or broker responses, and it does not imply that a
session can be restored after a process restart.
"""
from __future__ import annotations

from threading import RLock

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


broker_sessions = BrokerSessionStore()
