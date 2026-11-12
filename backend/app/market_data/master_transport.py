"""Bounded Angel One master transport with explicit TLS-version fallback.

The VPS diagnostics showed TCP establishment followed by a stalled TLS
ClientHello. Each request attempt therefore uses a fresh TLS context: system
negotiation first, then TLS 1.2 and TLS 1.3 explicitly. This can work around
TLS-version intolerance in middleboxes; it cannot bypass a route/firewall that
drops all TLS ServerHello traffic. Use GitHub-hosted artifact recovery for that
independent-egress case.
"""
from __future__ import annotations

import json
import ssl
import time
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter


MASTER_MAX_BYTES = 64 * 1024 * 1024
MASTER_CHUNK_BYTES = 1024 * 1024
MASTER_USER_AGENT = "Algo-Trading-InstrumentMaster/1.1"
TLS_PROFILES = ("system", "tls12", "tls13")


class _TLSAdapter(HTTPAdapter):
    def __init__(self, profile: str):
        self._profile = profile
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context()
        if self._profile == "tls12":
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.maximum_version = ssl.TLSVersion.TLSv1_2
        elif self._profile == "tls13":
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
        pool_kwargs["ssl_context"] = context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def _profile_request(url: str, *, timeout: Any, profile: str):
    session = requests.Session()
    adapter = _TLSAdapter(profile)
    session.mount("https://", adapter)
    try:
        response = session.get(
            url,
            timeout=timeout,
            stream=True,
            headers={"User-Agent": MASTER_USER_AGENT, "Accept": "application/json"},
        )
        # Keep the session attached so the caller can close its pool too.
        response._master_transport_session = session
        return response
    except Exception:
        session.close()
        raise


def download_master_rows(
    url: str,
    *,
    timeout: Any = (8, 90),
    retries: int = 3,
    backoff_seconds: float = 1.0,
    get: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch a complete JSON list using fresh TLS profiles, bounded memory and retries."""
    if retries < 1:
        raise ValueError("retries must be at least one")
    last_error: Exception | None = None
    for attempt in range(retries):
        response = None
        try:
            profile = TLS_PROFILES[min(attempt, len(TLS_PROFILES) - 1)]
            if get is None:
                response = _profile_request(url, timeout=timeout, profile=profile)
            else:
                response = get(
                    url, timeout=timeout, stream=True,
                    headers={"User-Agent": MASTER_USER_AGENT, "Accept": "application/json"},
                )
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=MASTER_CHUNK_BYTES):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MASTER_MAX_BYTES:
                    raise ValueError(f"instrument master exceeds {MASTER_MAX_BYTES} byte limit")
                chunks.append(chunk)
            if not chunks:
                raise ValueError("instrument master response is empty")
            payload = json.loads(b"".join(chunks).decode("utf-8"))
            if not isinstance(payload, list) or not payload:
                raise ValueError("instrument master must be a non-empty JSON list")
            for row in payload:
                if not isinstance(row, dict):
                    raise ValueError("instrument master contains a non-object row")
                for field in ("symbol", "exch_seg", "token"):
                    value = row.get(field)
                    if value is None or not str(value).strip():
                        raise ValueError(f"instrument master row is missing {field}")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(backoff_seconds * (2 ** attempt))
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                session = getattr(response, "_master_transport_session", None)
                if session is not None:
                    session.close()
    assert last_error is not None
    raise last_error
