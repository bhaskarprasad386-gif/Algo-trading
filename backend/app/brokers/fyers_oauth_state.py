from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit


@dataclass(frozen=True)
class PendingFyersAuthorization:
    app_id: str
    redirect_uri: str
    issued_at: float


class FyersOAuthStateStore:
    """Bounded, single-use OAuth state store for the FYERS redirect flow."""

    AUTH_URL = "https://api-t1.fyers.in/api/v3/generate-authcode"

    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        max_entries: int = 4096,
        clock=time.monotonic,
        token_factory=secrets.token_urlsafe,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("FYERS OAuth state limits must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._token_factory = token_factory
        self._pending: dict[str, PendingFyersAuthorization] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _validate_redirect_uri(redirect_uri: str) -> str:
        value = str(redirect_uri or "").strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("FYERS redirect URI must be a registered HTTPS URL")
        return value

    def create(self, *, app_id: str, redirect_uri: str) -> tuple[str, str]:
        app_id = str(app_id or "").strip()
        redirect_uri = self._validate_redirect_uri(redirect_uri)
        if not app_id:
            raise ValueError("FYERS app ID is required")
        state = self._token_factory(32)
        if not state:
            raise RuntimeError("Unable to generate FYERS OAuth state")
        now = self._clock()
        with self._lock:
            self._pending = {
                key: item
                for key, item in self._pending.items()
                if now - item.issued_at < self.ttl_seconds
            }
            if len(self._pending) >= self.max_entries:
                oldest = min(self._pending, key=lambda key: self._pending[key].issued_at)
                self._pending.pop(oldest, None)
            self._pending[state] = PendingFyersAuthorization(
                app_id=app_id,
                redirect_uri=redirect_uri,
                issued_at=now,
            )
        query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "state": state,
            }
        )
        return state, f"{self.AUTH_URL}?{query}"

    def consume(
        self, *, state: str, app_id: str, redirect_uri: str
    ) -> PendingFyersAuthorization:
        state = str(state or "").strip()
        app_id = str(app_id or "").strip()
        redirect_uri = self._validate_redirect_uri(redirect_uri)
        if not state or not app_id:
            raise ValueError("FYERS OAuth state and app ID are required")
        now = self._clock()
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or now - pending.issued_at >= self.ttl_seconds:
            raise ValueError("FYERS OAuth state is missing, expired, or already used")
        if pending.app_id != app_id or pending.redirect_uri != redirect_uri:
            raise ValueError("FYERS OAuth state does not match this app and redirect URI")
        return pending
