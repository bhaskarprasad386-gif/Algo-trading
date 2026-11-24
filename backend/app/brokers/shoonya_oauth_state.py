from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PendingShoonyaOAuth:
    owner_id: int
    client_id: str
    redirect_uri: str
    uid: str
    account_id: str
    issued_at: float


class ShoonyaOAuthFlowStore:
    """Bounded, expiring, single-use owner flow correlation for Shoonya OAuth."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        max_entries: int = 4096,
        clock=time.monotonic,
        token_factory=secrets.token_urlsafe,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("Shoonya OAuth flow limits must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._token_factory = token_factory
        self._pending: dict[str, PendingShoonyaOAuth] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _redirect(value: str) -> str:
        uri = str(value or "").strip()
        parsed = urlsplit(uri)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("Shoonya callback must be the registered HTTPS redirect URI")
        return uri

    def create(
        self,
        *,
        owner_id: int,
        client_id: str,
        redirect_uri: str,
        uid: str,
        account_id: str,
    ) -> str:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 0:
            raise ValueError("Shoonya OAuth owner ID is invalid")
        client_id = str(client_id or "").strip()
        uid = str(uid or "").strip()
        account_id = str(account_id or "").strip()
        redirect_uri = self._redirect(redirect_uri)
        if not all((client_id, uid, account_id)):
            raise ValueError("Shoonya client ID, UID and account ID are required")
        flow_id = str(self._token_factory(32) or "").strip()
        if not flow_id:
            raise RuntimeError("Unable to create Shoonya OAuth flow ID")
        now = self._clock()
        with self._lock:
            self._pending = {
                key: item
                for key, item in self._pending.items()
                if now - item.issued_at < self.ttl_seconds
            }
            # A token-factory collision must never overwrite an outstanding
            # browser flow or evict another user's flow to make room for it.
            if flow_id in self._pending:
                raise RuntimeError("Shoonya OAuth flow ID collision")
            if len(self._pending) >= self.max_entries:
                oldest = min(self._pending, key=lambda key: self._pending[key].issued_at)
                self._pending.pop(oldest, None)
            self._pending[flow_id] = PendingShoonyaOAuth(
                owner_id=owner_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                uid=uid,
                account_id=account_id,
                issued_at=now,
            )
        return flow_id

    def consume(
        self,
        *,
        owner_id: int,
        flow_id: str,
        client_id: str,
        redirect_uri: str,
    ) -> PendingShoonyaOAuth:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 0:
            raise ValueError("Shoonya OAuth owner ID is invalid")
        flow_id = str(flow_id or "").strip()
        client_id = str(client_id or "").strip()
        redirect_uri = self._redirect(redirect_uri)
        now = self._clock()
        with self._lock:
            pending = self._pending.get(flow_id)
            if pending is None or now - pending.issued_at >= self.ttl_seconds:
                self._pending.pop(flow_id, None)
                raise ValueError("Shoonya OAuth flow is missing, expired, or already used")
            if pending.owner_id != owner_id:
                raise ValueError("Shoonya OAuth flow does not belong to this owner")
            if pending.client_id != client_id or pending.redirect_uri != redirect_uri:
                # A mismatched attempt must not let a caller invalidate the
                # legitimate browser flow for this owner.
                raise ValueError("Shoonya OAuth flow does not match this client and callback")
            return self._pending.pop(flow_id)
