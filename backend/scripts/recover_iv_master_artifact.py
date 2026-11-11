"""Install one GitHub Actions IV master artifact after full integrity/freshness checks.

Run from the backend directory:
  export GITHUB_TOKEN="$(gh auth token)"
  PYTHONPATH=. python scripts/recover_iv_master_artifact.py \
    --artifact-id 13847286083 \
    --sha256 ee592b3a0e92ea536d9522253787dcd2de86ed6750d142128625993fdec65bb1

The token is read only from the environment and is never logged or persisted.
The artifact's original fetched_at is preserved; recovery never refreshes age.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.scanner.iv_skew_flip.instruments import (
    CACHE_SCHEMA_VERSION,
    MASTER_MAX_BYTES,
    MAX_CACHE_AGE_SECONDS,
    _canonical_rows_digest,
    _read_recent_snapshot,
    _validate_rows,
    _write_snapshot,
)

REPOSITORY = "bhaskarprasad386-gif/Algo-trading"
EXPECTED_MEMBER = "iv_instrument_master_cache.json"
API_BASE = "https://api.github.com"


def _download_artifact(artifact_id: int, token: str) -> bytes:
    url = f"{API_BASE}/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=(10, 120),
        stream=True,
    )
    try:
        response.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MASTER_MAX_BYTES:
                raise ValueError(f"artifact ZIP exceeds {MASTER_MAX_BYTES} byte limit")
            chunks.append(chunk)
        if not chunks:
            raise ValueError("GitHub returned an empty artifact ZIP")
        return b"".join(chunks)
    finally:
        response.close()


def _extract_verified_snapshot(archive_bytes: bytes, expected_sha256: str) -> tuple[list[dict[str, Any]], datetime]:
    actual_archive_digest = hashlib.sha256(archive_bytes).hexdigest()
    if actual_archive_digest.lower() != expected_sha256.lower():
        raise ValueError(
            f"artifact ZIP SHA-256 mismatch: expected {expected_sha256}, got {actual_archive_digest}"
        )

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = archive.namelist()
        if names != [EXPECTED_MEMBER]:
            raise ValueError(f"unexpected artifact members: {names!r}")
        info = archive.getinfo(EXPECTED_MEMBER)
        if info.file_size <= 0 or info.file_size > MASTER_MAX_BYTES:
            raise ValueError(f"instrument snapshot size is invalid: {info.file_size}")
        payload = json.loads(archive.read(info).decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("snapshot root must be a JSON object")
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise ValueError(
            f"schema version mismatch: expected {CACHE_SCHEMA_VERSION}, got {payload.get('schema_version')!r}"
        )

    fetched_text = payload.get("fetched_at")
    if not isinstance(fetched_text, str) or not fetched_text.strip():
        raise ValueError("snapshot fetched_at is missing")
    fetched_at = datetime.fromisoformat(fetched_text.replace("Z", "+00:00"))
    if fetched_at.tzinfo is None:
        raise ValueError("snapshot fetched_at must include a timezone")

    age = (datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > MAX_CACHE_AGE_SECONDS:
        raise ValueError(
            f"snapshot age is outside the 36-hour policy: age_seconds={age:.1f}"
        )

    rows = _validate_rows(payload.get("instruments"))
    expected_content_digest = str(payload.get("content_sha256", "")).lower()
    actual_content_digest = _canonical_rows_digest(rows)
    if expected_content_digest != actual_content_digest:
        raise ValueError(
            f"snapshot content SHA-256 mismatch: expected {expected_content_digest}, got {actual_content_digest}"
        )
    return rows, fetched_at.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-id", type=int, help="GitHub Actions artifact ID (use with a token)")
    parser.add_argument("--archive-path", type=Path, help="Previously downloaded ZIP; useful after GitHub artifact expiry")
    parser.add_argument("--sha256", required=True, help="GitHub artifact ZIP SHA-256 from Actions metadata")
    args = parser.parse_args()
    if bool(args.artifact_id) == bool(args.archive_path):
        parser.error("provide exactly one of --artifact-id or --archive-path")

    try:
        if args.archive_path:
            archive = args.archive_path.read_bytes()
            if len(archive) > MASTER_MAX_BYTES:
                raise ValueError(f"artifact ZIP exceeds {MASTER_MAX_BYTES} byte limit")
        else:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if not token:
                parser.error("set GITHUB_TOKEN or GH_TOKEN; do not put the token on the command line")
            archive = _download_artifact(args.artifact_id, token)
        rows, fetched_at = _extract_verified_snapshot(archive, args.sha256)
        _write_snapshot(rows, fetched_at=fetched_at)
        installed = _read_recent_snapshot()
        if installed is None:
            raise RuntimeError("installed snapshot failed its post-write integrity/freshness verification")
        installed_rows, installed_at = installed
        if installed_at != fetched_at or _canonical_rows_digest(installed_rows) != _canonical_rows_digest(rows):
            raise RuntimeError("installed snapshot does not match the verified artifact")
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        print(
            "IV instrument snapshot installed atomically: "
            f"rows={len(rows)} fetched_at={fetched_at.isoformat()} age_seconds={age:.1f} "
            f"content_sha256={_canonical_rows_digest(rows)}"
        )
        print("Box Spread cache was not modified.")
        return 0
    except Exception as exc:
        print(f"IV artifact recovery rejected; existing cache left unchanged: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
