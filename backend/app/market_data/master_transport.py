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
import socket
import ssl
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection


MASTER_MAX_BYTES = 64 * 1024 * 1024
MASTER_CHUNK_BYTES = 1024 * 1024
MASTER_USER_AGENT = "Algo-Trading-InstrumentMaster/1.1"
TLS_PROFILES = ("system", "tls12", "tls13")
# The VPS packet capture proved that Angel One's TLS response is segmented
# beyond what the VPS path reliably delivers. TCP_MAXSEG advertises a smaller
# receive MSS in the SYN, matching the successful 1100-byte MSS experiment.
# Apply only to the two official instrument-master hosts, never broker order,
# quote, WebSocket, or unrelated HTTPS connections.
ANGEL_MASTER_HOSTS = frozenset({
    "margincalculator.angelbroking.com",
    "margincalculator.angelone.in",
})
ANGEL_MASTER_TCP_MSS = 1100


class _TLSAdapter(HTTPAdapter):
    def __init__(self, profile: str, *, tcp_mss: int | None = None):
        self._profile = profile
        self._tcp_mss = tcp_mss
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context()
        if self._tcp_mss is not None:
            # urllib3 applies socket_options before connect(), so Linux places
            # the reduced MSS in the SYN and the remote server segments its
            # TLS flight accordingly. Preserve urllib3's standard options.
            pool_kwargs["socket_options"] = [
                *HTTPConnection.default_socket_options,
                (socket.IPPROTO_TCP, socket.TCP_MAXSEG, self._tcp_mss),
            ]
        if self._profile == "tls12":
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.maximum_version = ssl.TLSVersion.TLSv1_2
        elif self._profile == "tls13":
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
        pool_kwargs["ssl_context"] = context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def _tcp_mss_for_url(url: str) -> int | None:
    host = (urlsplit(url).hostname or "").lower()
    return ANGEL_MASTER_TCP_MSS if host in ANGEL_MASTER_HOSTS else None


def _profile_request(url: str, *, timeout: Any, profile: str):
    headers = {"User-Agent": MASTER_USER_AGENT, "Accept": "application/json"}
    tcp_mss = _tcp_mss_for_url(url)
    # Use a session for all TLS profiles so the narrowly scoped TCP MSS
    # mitigation also applies to the initial system-negotiated attempt.
    # Default SSLContext retains normal certificate verification and OS CAs.
    session = requests.Session()
    adapter = _TLSAdapter(profile, tcp_mss=tcp_mss)
    session.mount("https://", adapter)
    try:
        response = session.get(url, timeout=timeout, stream=True, headers=headers)
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
            if not callable(getattr(response, "iter_content", None)):
                # Narrow compatibility for lightweight legacy response doubles;
                # real requests.Response always supports bounded streaming.
                payload = response.json()
            else:
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
                # Cleanup must never mask the transport/validation exception or
                # turn a fully validated payload into a failed refresh. Always
                # attempt to close the TLS session even if response.close fails.
                session = getattr(response, "_master_transport_session", None)
                try:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    pass
                finally:
                    if session is not None:
                        try:
                            session.close()
                        except Exception:
                            pass
    assert last_error is not None
    raise last_error
