"""Atomic single-owner gate for Box Spread live publishing."""
from __future__ import annotations

import threading
from typing import Callable, TypeVar

T = TypeVar("T")


class BoxSpreadPublicationGate:
    """Serialize publication and ownership transfer across overlapping sessions."""

    def __init__(self):
        self._lock = threading.RLock()
        self._owner = None

    def activate(self, owner) -> None:
        if owner is None:
            raise ValueError("Box Spread publisher owner is required")
        with self._lock:
            self._owner = owner

    def deactivate(self, owner) -> bool:
        with self._lock:
            if self._owner is not owner:
                return False
            self._owner = None
            return True

    def claim_if_unowned(self, owner) -> bool:
        """Claim an idle gate, or confirm this runner already owns it."""
        if owner is None:
            return False
        with self._lock:
            if self._owner is None:
                self._owner = owner
            return self._owner is owner

    def is_owner(self, owner) -> bool:
        with self._lock:
            return owner is not None and self._owner is owner

    def publish(self, owner, callback: Callable[[], T]) -> bool:
        """Run all publication side effects under the ownership lock."""
        with self._lock:
            if owner is None or self._owner is not owner:
                return False
            callback()
            return True


BOX_SPREAD_PUBLICATION_GATE = BoxSpreadPublicationGate()

__all__ = ["BoxSpreadPublicationGate", "BOX_SPREAD_PUBLICATION_GATE"]
