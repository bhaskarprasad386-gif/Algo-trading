from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Dict, Optional


@dataclass
class BrokerConnection:
    user_id: int
    broker: str
    connected: bool = False
    display_name: Optional[str] = None
    connected_at: Optional[datetime] = None


class BrokerConnectionStore:
    """In-memory connection metadata store; broker secrets/tokens are never persisted here."""

    def __init__(self) -> None:
        self._items: Dict[tuple[int, str], BrokerConnection] = {}
        self._lock = RLock()

    def connect(self, user_id: int, broker: str, display_name: str | None = None) -> BrokerConnection:
        item = BrokerConnection(
            user_id=user_id,
            broker=broker,
            connected=True,
            display_name=display_name,
            connected_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._items[(user_id, broker)] = item
        return item

    def disconnect(self, user_id: int, broker: str) -> None:
        with self._lock:
            self._items.pop((user_id, broker), None)

    def get(self, user_id: int, broker: str) -> BrokerConnection | None:
        with self._lock:
            return self._items.get((user_id, broker))

    def list(self, user_id: int) -> list[BrokerConnection]:
        with self._lock:
            return [v for (uid, _), v in self._items.items() if uid == user_id]


broker_connections = BrokerConnectionStore()
