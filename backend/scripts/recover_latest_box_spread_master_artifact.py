"""Recover only the Box Spread cache from the latest successful main-branch artifact.

This deliberately does not require, inspect, or modify the IV snapshot.
Requires a repository-scoped GitHub token with Actions: read permission.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests

from app.market_data import box_spread_instrument_cache as box
from recover_box_spread_master_artifact import _extract

REPOSITORY = "bhaskarprasad386-gif/Algo-trading"
API_BASE = "https://api.github.com"
ARTIFACT_NAME = "box-spread-instrument-master-validated"
MAX_ARCHIVE_BYTES = box.MASTER_MAX_BYTES


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_json(url: str, token: str) -> dict[str, Any]:
    response = requests.get(url, headers=_headers(token), timeout=(10, 30))
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    if not isinstance(payload, dict):
        raise ValueError("GitHub API returned a non-object response")
    return payload


def _latest_box_artifact(token: str) -> dict[str, Any]:
    payload = _get_json(
        f"{API_BASE}/repos/{REPOSITORY}/actions/artifacts?per_page=100",
        token,
    )
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("GitHub artifact listing is malformed")
    candidates = [
        item for item in artifacts
        if isinstance(item, dict)
        and item.get("name") == ARTIFACT_NAME
        and item.get("expired") is False
        and isinstance(item.get("id"), int)
        and isinstance(item.get("created_at"), str)
        and isinstance(item.get("workflow_run"), dict)
        and item["workflow_run"].get("head_branch") == "main"
        and isinstance(item["workflow_run"].get("id"), int)
    ]
    verified = []
    for item in candidates:
        run = _get_json(
            f"{API_BASE}/repos/{REPOSITORY}/actions/runs/{item['workflow_run']['id']}",
            token,
        )
        if (
            run.get("head_branch") == "main"
            and run.get("conclusion") == "success"
            and run.get("status") == "completed"
            and run.get("head_sha") == item["workflow_run"].get("head_sha")
        ):
            verified.append(item)
    if not verified:
        raise RuntimeError("No unexpired Box Spread artifact from a successful main-branch workflow exists")
    verified.sort(key=lambda item: item["created_at"], reverse=True)
    selected = verified[0]
    digest = selected.get("digest")
    if not isinstance(digest, str) or not digest.lower().startswith("sha256:"):
        raise ValueError("GitHub Box Spread artifact has no SHA-256 digest")
    selected["sha256"] = digest.split(":", 1)[1].lower()
    return selected


def _download_zip(artifact_id: int, token: str) -> bytes:
    response = requests.get(
        f"{API_BASE}/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip",
        headers=_headers(token),
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
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError(f"Artifact ZIP exceeds {MAX_ARCHIVE_BYTES} byte limit")
            chunks.append(chunk)
        if not chunks:
            raise ValueError("GitHub returned an empty artifact ZIP")
        return b"".join(chunks)
    finally:
        response.close()


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("Set GITHUB_TOKEN or GH_TOKEN with Actions: read access.", file=sys.stderr)
        return 2
    try:
        metadata = _latest_box_artifact(token)
        archive = _download_zip(metadata["id"], token)
        rows, fetched_at = _extract(archive, metadata["sha256"])
        box._write_cache(rows, fetched_at=fetched_at)
        installed = box._read_cache()
        if (
            installed is None
            or installed[1] != fetched_at
            or box._canonical_rows_digest(installed[0]) != box._canonical_rows_digest(rows)
        ):
            raise RuntimeError("Box Spread cache read-back integrity/freshness verification failed")
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        print(
            f"Box Spread recovery PASS artifact_id={metadata['id']} "
            f"rows={len(rows)} fetched_at={fetched_at.isoformat()} "
            f"age_seconds={age:.1f} sha256={box._canonical_rows_digest(rows)}"
        )
        print("IV cache, trading runners, and WebSocket services were not touched.")
        return 0
    except Exception as exc:
        print(f"Box Spread artifact recovery failed; existing cache left unchanged: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
