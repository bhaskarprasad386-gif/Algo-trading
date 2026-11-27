"""Recover the validated IV master artifact for IV and shared index market data from GitHub Actions.

Requires GITHUB_TOKEN or GH_TOKEN with Actions: read access. Does not restart
services, change runner state, or refresh the source fetched_at timestamps.
The IV snapshot and shared index master cache are independently verified.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import requests

from app.market_data.instruments import InstrumentMaster
from app.scanner.iv_skew_flip import instruments as iv
from recover_iv_master_artifact import _extract_verified_snapshot as extract_iv

REPOSITORY = "bhaskarprasad386-gif/Algo-trading"
API_BASE = "https://api.github.com"
ARTIFACT_NAME = "iv-instrument-master-validated"
MAX_ARCHIVE_BYTES = iv.MASTER_MAX_BYTES


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


def _latest_artifact(name: str, token: str) -> dict[str, Any]:
    # GitHub paginates artifact listings. Walk every page so a busy repository
    # cannot hide the newest eligible production artifact beyond page one.
    candidates: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _get_json(
            f"{API_BASE}/repos/{REPOSITORY}/actions/artifacts?per_page=100&page={page}",
            token,
        )
        artifacts = payload.get("artifacts")
        total_count = payload.get("total_count")
        if not isinstance(artifacts, list) or not isinstance(total_count, int):
            raise ValueError("GitHub artifact listing is malformed")
        # Never install an artifact produced by an arbitrary PR, fork, or failed
        # workflow. Only artifacts from a successful run on the protected main
        # branch are eligible for production recovery.
        candidates.extend(
            item for item in artifacts
            if isinstance(item, dict)
            and item.get("name") == name
            and item.get("expired") is False
            and isinstance(item.get("id"), int)
            and isinstance(item.get("created_at"), str)
            and isinstance(item.get("workflow_run"), dict)
            and item["workflow_run"].get("head_branch") == "main"
            and isinstance(item["workflow_run"].get("id"), int)
        )
        if not artifacts or page * 100 >= total_count:
            break
        page += 1

    candidates.sort(key=lambda item: item["created_at"], reverse=True)
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
            digest = item.get("digest")
            if not isinstance(digest, str) or not digest.lower().startswith("sha256:"):
                raise ValueError(f"Artifact {name} has no SHA-256 digest in GitHub metadata")
            item["sha256"] = digest.split(":", 1)[1].lower()
            return item

    raise RuntimeError(
        f"No unexpired artifact from a successful completed main-branch workflow found: {name}"
    )


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
        print("Set GITHUB_TOKEN or GH_TOKEN with Actions: read permission.", file=sys.stderr)
        return 2

    try:
        # Only the validated IV instrument artifact is authoritative for both
        # IV's own snapshot and the shared NIFTY/BANKNIFTY market-data master.
        metadata = _latest_artifact(ARTIFACT_NAME, token)
        archive = _download_zip(metadata["id"], token)
        rows, fetched_at = extract_iv(archive, metadata["sha256"])
        print(
            f"iv_artifact_verified id={metadata['id']} "
            f"created_at={metadata['created_at']} rows={len(rows)} "
            f"fetched_at={fetched_at.isoformat()}"
        )

        # Preserve the source timestamp in both caches; never make a stale
        # instrument artifact appear fresh.
        iv._write_snapshot(rows, fetched_at=fetched_at)
        iv_installed = iv._read_recent_snapshot()
        if (
            iv_installed is None
            or iv_installed[1] != fetched_at
            or iv._canonical_rows_digest(iv_installed[0]) != iv._canonical_rows_digest(rows)
        ):
            raise RuntimeError("IV cache read-back verification failed")
        print(f"iv_install=PASS rows={len(rows)} fetched_at={fetched_at.isoformat()}")

        InstrumentMaster._write_disk_snapshot(rows, fetched_at=fetched_at)
        shared_installed = InstrumentMaster._read_disk_snapshot()
        if (
            shared_installed is None
            or shared_installed[1] != fetched_at
            or shared_installed[0] != rows
        ):
            raise RuntimeError("Shared InstrumentMaster cache read-back verification failed")
        print(
            f"shared_master_install=PASS rows={len(rows)} "
            f"fetched_at={fetched_at.isoformat()}"
        )

        print("Recovery complete. No services or live scanners were restarted.")
        return 0
    except Exception as exc:
        print(
            f"Artifact recovery failed: {type(exc).__name__}: {exc}. "
            "The validated IV snapshot and shared master are independently verified; inspect both cache states before retrying.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
