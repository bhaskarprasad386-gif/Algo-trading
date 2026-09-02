"""Small deterministic idempotency guard for execution requests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionRequest:
    key: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("idempotency key cannot be empty")


class IdempotencyGuard:
    """Ensure an execution key is accepted at most once."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def accept(self, request: ExecutionRequest) -> bool:
        if request.key in self._seen:
            return False
        self._seen.add(request.key)
        return True
