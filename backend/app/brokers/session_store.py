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
        self._replacement_generations: dict[tuple[int, str], int] = {}
        self._lock = RLock()

    @staticmethod
    def _owner(owner_id: int) -> int:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 0:
            raise ValueError("owner ID must be a non-negative integer")
        return owner_id

    @staticmethod
    def _key(owner_id: int, broker: str) -> tuple[int, str]:
        owner = BrokerSessionStore._owner(owner_id)
        normalized = str(broker).strip().lower()
        if not normalized:
            raise ValueError("broker name cannot be empty")
        return owner, normalized

    def reserve_replacement(self, owner_id: int, broker: str) -> int:
        """Reserve reconnect ordering before a slow OAuth/network exchange."""
        key = self._key(owner_id, broker)
        with self._lock:
            generation = self._replacement_generations.get(key, 0) + 1
            self._replacement_generations[key] = generation
            return generation

    def replace_if_reserved(
        self, owner_id: int, broker: str, adapter: BrokerAdapter, generation: int
    ) -> tuple[bool, BrokerAdapter | None]:
        """Install only if the caller still owns the latest reconnect reservation."""
        key = self._key(owner_id, broker)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise ValueError("replacement generation must be a positive integer")
        adapter_name = str(getattr(adapter, "name", "")).strip().lower()
        if adapter_name != key[1]:
            raise ValueError("adapter name does not match broker session key")
        with self._lock:
            if self._replacement_generations.get(key) != generation:
                return False, None
            self._replacement_generations[key] = generation + 1
            previous = self._sessions.get(key)
            self._sessions[key] = adapter
            return True, previous

    def replace(
        self, owner_id: int, broker: str, adapter: BrokerAdapter
    ) -> BrokerAdapter | None:
        key = self._key(owner_id, broker)
        adapter_name = str(getattr(adapter, "name", "")).strip().lower()
        if adapter_name != key[1]:
            raise ValueError("adapter name does not match broker session key")
        with self._lock:
            self._replacement_generations[key] = self._replacement_generations.get(key, 0) + 1
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
        if previous is None or previous is adapter:
            # Idempotent replacement must never disconnect the adapter that
            # remains installed as the current session.
            return None
        try:
            previous.disconnect()
        except Exception as exc:
            return exc
        return None

    def commit_if_current(
        self,
        owner_id: int,
        broker: str,
        adapter: BrokerAdapter,
        operation: Callable[[], None],
        *,
        generation: int | None = None,
    ) -> bool:
        """Run metadata commit only while the adapter and its reservation remain current."""
        key = self._key(owner_id, broker)
        with self._lock:
            if self._sessions.get(key) is not adapter:
                return False
            if generation is not None:
                if (
                    isinstance(generation, bool)
                    or not isinstance(generation, int)
                    or generation <= 0
                    or self._replacement_generations.get(key) != generation + 1
                ):
                    return False
            operation()
            return True

    def remove_if_current(
        self,
        owner_id: int,
        broker: str,
        adapter: BrokerAdapter,
        *,
        generation: int | None = None,
    ) -> bool:
        """Remove only the current adapter while its optional reservation is still owned."""
        key = self._key(owner_id, broker)
        with self._lock:
            if self._sessions.get(key) is not adapter:
                return False
            if generation is not None:
                if (
                    isinstance(generation, bool)
                    or not isinstance(generation, int)
                    or generation <= 0
                    or self._replacement_generations.get(key) != generation + 1
                ):
                    return False
            self._sessions.pop(key, None)
            self._replacement_generations[key] = self._replacement_generations.get(key, 0) + 1
            return True


    def get(self, owner_id: int, broker: str) -> BrokerAdapter | None:
        key = self._key(owner_id, broker)
        with self._lock:
            return self._sessions.get(key)

    def remove(self, owner_id: int, broker: str) -> BrokerAdapter | None:
        key = self._key(owner_id, broker)
        with self._lock:
            adapter = self._sessions.pop(key, None)
            self._replacement_generations[key] = self._replacement_generations.get(key, 0) + 1
            return adapter

    def clear_owner(self, owner_id: int) -> list[BrokerAdapter]:
        owner = self._owner(owner_id)
        with self._lock:
            keys = [key for key in self._sessions if key[0] == owner]
            reserved_keys = [key for key in self._replacement_generations if key[0] == owner]
            for key in set(keys) | set(reserved_keys):
                self._replacement_generations[key] = self._replacement_generations.get(key, 0) + 1
            return [self._sessions.pop(key) for key in keys]

    def clear(self) -> list[BrokerAdapter]:
        """Remove all in-memory sessions; intended for controlled shutdown/tests."""
        with self._lock:
            sessions = list(self._sessions.values())
            keys = set(self._sessions) | set(self._replacement_generations)
            for key in keys:
                self._replacement_generations[key] = self._replacement_generations.get(key, 0) + 1
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
