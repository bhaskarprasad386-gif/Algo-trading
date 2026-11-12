from __future__ import annotations

import pytest

from scripts import recover_latest_box_spread_master_artifact as recovery


def _artifact(artifact_id: int, created_at: str, *, branch: str = "main", sha: str = "abc"):
    return {
        "id": artifact_id,
        "name": recovery.ARTIFACT_NAME,
        "expired": False,
        "created_at": created_at,
        "digest": f"sha256:{artifact_id:064x}",
        "workflow_run": {
            "id": artifact_id + 1000,
            "head_branch": branch,
            "head_sha": sha,
        },
    }


def test_latest_artifact_selects_newest_successful_main_run_only(monkeypatch):
    listing = {
        "artifacts": [
            _artifact(10, "2026-11-12T00:00:00Z", branch="feature/untrusted"),
            _artifact(11, "2026-11-12T01:00:00Z", sha="failed-sha"),
            _artifact(12, "2026-11-12T02:00:00Z", sha="good-sha"),
            _artifact(13, "2026-11-12T03:00:00Z", sha="newer-failed-sha"),
        ]
    }
    runs = {
        1010: {"head_branch": "feature/untrusted", "conclusion": "success", "status": "completed", "head_sha": "abc"},
        1011: {"head_branch": "main", "conclusion": "failure", "status": "completed", "head_sha": "failed-sha"},
        1012: {"head_branch": "main", "conclusion": "success", "status": "completed", "head_sha": "good-sha"},
        1013: {"head_branch": "main", "conclusion": "in_progress", "status": "in_progress", "head_sha": "newer-failed-sha"},
    }

    def fake_get_json(url: str, token: str):
        if url.endswith("/actions/artifacts?per_page=100"):
            return listing
        return runs[int(url.rsplit("/", 1)[1])]

    monkeypatch.setattr(recovery, "_get_json", fake_get_json)
    selected = recovery._latest_box_artifact("test-token")

    assert selected["id"] == 12
    assert selected["sha256"] == f"{12:064x}"


def test_latest_artifact_rejects_when_no_successful_main_artifact(monkeypatch):
    monkeypatch.setattr(
        recovery,
        "_get_json",
        lambda url, token: (
            {"artifacts": [_artifact(20, "2026-11-12T00:00:00Z", branch="feature/unsafe")]}
            if url.endswith("/actions/artifacts?per_page=100")
            else {"head_branch": "feature/unsafe", "conclusion": "success", "status": "completed", "head_sha": "abc"}
        ),
    )

    with pytest.raises(RuntimeError, match="No unexpired Box Spread artifact"):
        recovery._latest_box_artifact("test-token")


def test_latest_artifact_rejects_missing_digest(monkeypatch):
    item = _artifact(30, "2026-11-12T00:00:00Z")
    item.pop("digest")
    monkeypatch.setattr(
        recovery,
        "_get_json",
        lambda url, token: (
            {"artifacts": [item]}
            if url.endswith("/actions/artifacts?per_page=100")
            else {"head_branch": "main", "conclusion": "success", "status": "completed", "head_sha": "abc"}
        ),
    )

    with pytest.raises(ValueError, match="no SHA-256 digest"):
        recovery._latest_box_artifact("test-token")
