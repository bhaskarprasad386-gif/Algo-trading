"""Recover the newest validated Box Spread and IV master artifacts from GitHub Actions.

Requires GITHUB_TOKEN or GH_TOKEN with Actions: read access. Does not restart
services, change runner state, or refresh the source fetched_at timestamps.
Each cache is independently validated and atomically replaced.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import requests

from app.market_data import box_spread_instrument_cache as box
from app.scanner.iv_skew_flip import instruments as iv
from recover_box_spread_master_artifact import _extract as extract_box
from recover_iv_master_artifact import _extract_verified_snapshot as extract_iv

REPOSITORY = "bhaskarprasad386-gif/Algo-trading"
API_BASE = "https://api.github.com"
ARTIFACT_NAMES = {
    "box": "box-spread-instrument-master-validated",
    "iv": "iv-instrument-master-validated",
}
MAX_ARCHIVE_BYTES = max(box.MASTER_MAX_BYTES, iv.MASTER_MAX_BYTES)


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
    payload = _get_json(
        f"{API_BASE}/repos/{REPOSITORY}/actions/artifacts?per_page=100",
        token,
    )
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("GitHub artifact listing is malformed")
    matches = [
        item for item in artifacts
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("expired") is False
        and isinstance(item.get("id"), int)
        and isinstance(item.get("created_at"), str)
    ]
    if not matches:
        raise RuntimeError(f"No unexpired GitHub Actions artifact found: {name}")
    matches.sort(key=lambda item: item["created_at"], reverse=True)
    selected = matches[0]
    digest = selected.get("digest")
    if not isinstance(digest, str) or not digest.lower().startswith("sha256:"):
        raise ValueError(f"Artifact {name} has no SHA-256 digest in GitHub metadata")
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
        print("Set GITHUB_TOKEN or GH_TOKEN with Actions: read permission.", file=sys.stderr)
        return 2

    try:
        # Validate both archives fully before either isolated cache is changed.
        prepared = {}
        for key, artifact_name in ARTIFACT_NAMES.items():
            metadata = _latest_artifact(artifact_name, token)
            archive = _download_zip(metadata["id"], token)
            if key == "box":
                rows, fetched_at = extract_box(archive, metadata["sha256"])
            else:
                rows, fetched_at = extract_iv(archive, metadata["sha256"])
            prepared[key] = (rows, fetched_at, metadata)
            print(
                f"{key}_artifact_verified id={metadata['id']} "
                f"created_at={metadata['created_at']} rows={len(rows)} "
                f"fetched_at={fetched_at.isoformat()}"
            )

        box_rows, box_fetched, _ = prepared["box"]
        box._write_cache(box_rows, fetched_at=box_fetched)
        box_installed = box._read_cache()
        if (
            box_installed is None
            or box_installed[1] != box_fetched
            or box._canonical_rows_digest(box_installed[0]) != box._canonical_rows_digest(box_rows)
        ):
            raise RuntimeError("Box Spread cache read-back verification failed")
        print(f"box_install=PASS rows={len(box_rows)} fetched_at={box_fetched.isoformat()}")

        iv_rows, iv_fetched, _ = prepared["iv"]
        iv._write_snapshot(iv_rows, fetched_at=iv_fetched)
        iv_installed = iv._read_recent_snapshot()
        if (
            iv_installed is None
            or iv_installed[1] != iv_fetched
            or iv._canonical_rows_digest(iv_installed[0]) != iv._canonical_rows_digest(iv_rows)
        ):
            raise RuntimeError("IV cache read-back verification failed")
        print(f"iv_install=PASS rows={len(iv_rows)} fetched_at={iv_fetched.isoformat()}")
        print("Recovery complete. No services or live scanners were restarted.")
        return 0
    except Exception as exc:
        print(
            f"Artifact recovery failed: {type(exc).__name__}: {exc}. "
            "Each cache is validated before install; inspect both cache states before retrying.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
