"""Human-in-the-loop live-order confirmation with a bounded TTL."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ConfirmationRequest:
    request_id: str
    created_at: datetime
    expires_at: datetime


class ConfirmationGateway:
    """Issue and validate explicit confirmations before live execution."""

    def __init__(self, ttl_seconds: int = 30) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl = timedelta(seconds=ttl_seconds)
        self._requests: dict[str, ConfirmationRequest] = {}

    def create(self, request_id: str, now: datetime | None = None) -> ConfirmationRequest:
        if not request_id:
            raise ValueError("request_id is required")
        current = _utc(now)
        request = ConfirmationRequest(request_id, current, current + self.ttl)
        self._requests[request_id] = request
        return request

    def confirm(self, request_id: str, now: datetime | None = None) -> bool:
        request = self._requests.get(request_id)
        if request is None:
            return False
        current = _utc(now)
        if current >= request.expires_at:
            self._requests.pop(request_id, None)
            return False
        self._requests.pop(request_id, None)
        return True


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)
