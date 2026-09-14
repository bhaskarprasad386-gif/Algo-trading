"""Deterministic retry/fallback policy for execution adapters."""

from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


class RetryExhaustedError(RuntimeError):
    """Raised when all execution attempts fail."""


def execute_with_retry(
    operation: Callable[[], T],
    policy: RetryPolicy | None = None,
    fallback: Callable[[], T] | None = None,
) -> T:
    """Execute once by default; retries require an explicitly retry-safe operation."""
    active_policy = policy or RetryPolicy()
    attempts = active_policy.max_attempts if active_policy.retryable else 1
    last_error: Exception | None = None

    for _ in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc

    if fallback is not None:
        return fallback()

    raise RetryExhaustedError("execution failed after retry policy") from last_error
