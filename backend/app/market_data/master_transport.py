"""Bounded and retryable transport for the official Angel One instrument master.

Retries help transient HTTP/network failures; they cannot repair a TLS handshake
that never receives a ServerHello. Callers must retain last-known-good data.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests


MASTER_MAX_BYTES = 64 * 1024 * 1024
MASTER_CHUNK_BYTES = 1024 * 1024
MASTER_USER_AGENT = "Algo-Trading-InstrumentMaster/1.0"


def download_master_rows(
    url: str,
    *,
    timeout: Any = (10, 120),
    retries: int = 3,
    backoff_seconds: float = 1.0,
    get=None,
) -> list[dict[str, Any]]:
    """Fetch a complete JSON list with bounded memory, retries and strict shape checks."""
    if get is None:
        get = requests.get
    if retries < 1:
        raise ValueError("retries must be at least one")
    last_error: Exception | None = None
    for attempt in range(retries):
        response = None
        try:
            response = get(
                url,
                timeout=timeout,
                stream=True,
                headers={
                    "User-Agent": MASTER_USER_AGENT,
                    "Accept": "application/json",
                },
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
    assert last_error is not None
    raise last_error
