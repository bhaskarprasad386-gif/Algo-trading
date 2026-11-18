"""Install a Box Spread-only Angel One master artifact after integrity/freshness checks.

The archive must contain exactly box_spread_instrument_master_cache.json.
The original fetched_at is preserved; recovery never refreshes cache age.
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

from app.market_data import box_spread_instrument_cache as cache


EXPECTED_MEMBER = "box_spread_instrument_master_cache.json"


def _extract(archive_bytes: bytes, expected_sha256: str):
    actual = hashlib.sha256(archive_bytes).hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise ValueError(f"artifact ZIP SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        if archive.namelist() != [EXPECTED_MEMBER]:
            raise ValueError(f"unexpected artifact members: {archive.namelist()!r}")
        info = archive.getinfo(EXPECTED_MEMBER)
        if info.file_size <= 0 or info.file_size > cache.MASTER_MAX_BYTES:
            raise ValueError(f"invalid instrument snapshot size: {info.file_size}")
        payload = json.loads(archive.read(info).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != cache.CACHE_SCHEMA_VERSION:
        raise ValueError("Box Spread cache schema version mismatch")
    rows = cache._validated_rows(payload.get("instruments"))
    if rows is None:
        raise ValueError("Box Spread instrument rows failed validation")
    digest = cache._canonical_rows_digest(rows)
    if str(payload.get("content_sha256", "")).lower() != digest:
        raise ValueError("Box Spread instrument content SHA-256 mismatch")
    fetched_text = payload.get("fetched_at")
    if not isinstance(fetched_text, str) or not fetched_text.strip():
        raise ValueError("Box Spread fetched_at is missing")
    fetched = datetime.fromisoformat(fetched_text.replace("Z", "+00:00"))
    if fetched.tzinfo is None:
        raise ValueError("Box Spread fetched_at must include timezone")
    age = (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > cache.MAX_CACHE_AGE_SECONDS:
        raise ValueError(f"Box Spread artifact age outside 36-hour policy: {age:.1f}s")
    return rows, fetched.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-path", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        archive = args.archive_path.read_bytes()
        if len(archive) > cache.MASTER_MAX_BYTES:
            raise ValueError("artifact ZIP exceeds maximum allowed size")
        rows, fetched = _extract(archive, args.sha256)
        cache._write_cache(rows, fetched_at=fetched)
        installed = cache._read_cache()
        if installed is None or installed[1] != fetched or cache._canonical_rows_digest(installed[0]) != cache._canonical_rows_digest(rows):
            raise RuntimeError("installed Box Spread cache failed read-back integrity/freshness verification")
        print(
            "Box Spread instrument cache installed: "
            f"rows={len(rows)} fetched_at={fetched.isoformat()} "
            f"age_seconds={(datetime.now(timezone.utc)-fetched).total_seconds():.1f} "
            f"content_sha256={cache._canonical_rows_digest(rows)}"
        )
        print("IV instrument snapshot was not modified.")
        return 0
    except Exception as exc:
        print(f"Box Spread artifact recovery rejected; existing cache left unchanged: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
