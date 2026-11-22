"""Regression tests for bounded VPS deployment Git fetch retries."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "vps-deploy.yml"


def _remote_script(workflow: str) -> str:
    marker = "cat > /tmp/vps_remote_deploy.sh <<'REMOTE_SCRIPT'\n"
    assert marker in workflow, "remote deploy script must be present"
    return workflow.split(marker, 1)[1].split("\nREMOTE_SCRIPT", 1)[0]


def _fetch_function(workflow: str) -> str:
    match = re.search(r"git_fetch_with_retry\(\)\s*\{(.*?)\n\s*\}", _remote_script(workflow), re.S)
    assert match, "remote deploy must define a bounded Git fetch retry function"
    return "git_fetch_with_retry() {" + match.group(1) + "\n}"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_fetch_function(function_source: str, git_fails: int, temp: Path) -> subprocess.CompletedProcess[str]:
    calls = temp / "fetch_calls"
    bin_dir = temp / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_executable(
        bin_dir / "git",
        "#!/bin/bash\n"
        "set -eu\n"
        "count=$(cat \"$GIT_FETCH_CALLS_FILE\" 2>/dev/null || echo 0)\n"
        "count=$((count + 1))\n"
        "printf '%s\n' \"$count\" > \"$GIT_FETCH_CALLS_FILE\"\n"
        "if (( count <= GIT_FAILS )); then exit 56; fi\n"
        "exit 0\n",
    )
    _write_executable(bin_dir / "timeout", "#!/bin/bash\nshift\nexec \"$@\"\n")
    _write_executable(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")
    env = os.environ.copy()
    env.update(
        PATH=f"{bin_dir}:{env.get('PATH', '')}",
        GIT_FAILS=str(git_fails),
        GIT_FETCH_CALLS_FILE=str(calls),
    )
    return subprocess.run(
        ["bash", "-c", function_source + "\ngit_fetch_with_retry"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )


def test_git_fetch_retries_transient_failure_then_succeeds():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    function_source = _fetch_function(workflow)
    with TemporaryDirectory() as tmp:
        temp = Path(tmp)
        result = _run_fetch_function(function_source, git_fails=2, temp=temp)
        assert result.returncode == 0, result.stderr
        assert (temp / "fetch_calls").read_text().strip() == "3"
        assert "GIT_FETCH_SUCCESS attempt=3" in result.stdout


def test_git_fetch_failure_is_bounded_and_fails_closed_before_merge():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    remote = _remote_script(workflow)
    function_source = _fetch_function(workflow)
    assert "timeout 300s git fetch origin main" in function_source
    assert "for attempt in 1 2 3" in function_source
    assert remote.index("git_fetch_with_retry || exit 1") < remote.index("git merge --ff-only origin/main")
    with TemporaryDirectory() as tmp:
        temp = Path(tmp)
        result = _run_fetch_function(function_source, git_fails=99, temp=temp)
        assert result.returncode == 1
        assert (temp / "fetch_calls").read_text().strip() == "3"
        assert "DEPLOY_ABORTED_GIT_FETCH_FAILED" in result.stderr
