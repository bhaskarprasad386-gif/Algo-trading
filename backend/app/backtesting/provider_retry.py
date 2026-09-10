"""Provider-aware retry and pacing policy for historical acquisition."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ProviderRetryPolicy:
    """Bound provider pacing and retry delays without retrying permanent errors."""

    min_interval_seconds: float = 0.0
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.25
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = random.random
    _last_attempt: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds cannot be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds cannot be below base_delay_seconds")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def before_attempt(self) -> None:
        now = self.clock()
        if self._last_attempt is not None:
            wait = self.min_interval_seconds - (now - self._last_attempt)
            if wait > 0:
                self.sleeper(wait)
                now = self.clock()
        self._last_attempt = now

    def delay(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        raw = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))
        if self.jitter_ratio == 0:
            return raw
        factor = 1 - self.jitter_ratio + (2 * self.jitter_ratio * self.random_fn())
        return raw * factor

    @staticmethod
    def is_transient(error: BaseException) -> bool:
        if isinstance(error, (TimeoutError, ConnectionError)):
            return True
        status = getattr(error, "status_code", None)
        if isinstance(status, int) and (status == 429 or 500 <= status < 600):
            return True
        name = type(error).__name__.lower()
        return name in {"ratelimiterror", "temporarilyunavailable"}


def build_provider_retry_policy(provider: str) -> ProviderRetryPolicy:
    """Return a conservative default policy for a supported historical provider."""
    name = provider.strip().lower()
    if name == "angelone":
        # Angel One historical requests are limited to 3 requests/second.
        return ProviderRetryPolicy(min_interval_seconds=1 / 3)
    raise ValueError(f"unsupported historical provider: {provider}")
