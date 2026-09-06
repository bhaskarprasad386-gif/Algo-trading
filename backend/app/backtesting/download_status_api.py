"""Read-only API helpers for durable historical-download status."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .historical_download_status import HistoricalDownloadStatusStore


def download_status_payload(store: HistoricalDownloadStatusStore, job_id: str) -> dict[str, Any]:
    """Return a JSON-safe snapshot of a download job and its persisted chunks."""
    job = store.job(job_id)
    if job is None:
        raise KeyError(job_id)
    chunks = store.chunks(job_id)
    return {
        "job": asdict(job),
        "chunks": [asdict(chunk) for chunk in chunks],
    }
