from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SnapshotStore(Generic[T]):
    """In-memory latest-snapshot boundary; replaceable by SQLite later."""

    value: T | None = None

    def put(self, value: T) -> "SnapshotStore[T]":
        return SnapshotStore(value=value)

    def get(self) -> T | None:
        return self.value
