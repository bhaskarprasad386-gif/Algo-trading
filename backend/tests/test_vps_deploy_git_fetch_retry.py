import os
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "vps-deploy.yml"
START_MARKER = "# BEGIN GIT_FETCH_RETRY_HELPER"
END_MARKER = "# END GIT_FETCH_RETRY_HELPER"


def _fetch_helper() -> str:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = workflow.index(START_MARKER)
    end = workflow.index(END_MARKER, start) + len(END_MARKER)
    return textwrap.dedent(workflow[start:end])


def _run_helper(tmp_path: Path, *, failures: int = 0, error: str = "transient"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    count_file = tmp_path / "fetch-count"

    (bin_dir / "timeout").write_text(
        "#!/usr/bin/env bash\nshift\nexec \"$@\"\n",
        encoding="utf-8",
    )
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "git").write_text(
        """#!/usr/bin/env bash
if [[ \"$1 $2 $3\" != \"fetch origin main\" ]]; then
  exit 90
fi
count=0
if [[ -f \"$FETCH_COUNT_FILE\" ]]; then count=$(cat \"$FETCH_COUNT_FILE\"); fi
count=$((count + 1))
printf \"%s\" \"$count\" > \"$FETCH_COUNT_FILE\"
if [[ \"$FETCH_ERROR\" == \"auth\" ]]; then
  printf 'fatal: Authentication failed\\n' >&2
  exit 128
fi
if (( count <= FETCH_FAILS )); then
  printf 'error: RPC failed; curl 56 Recv failure: Connection reset by peer\\n' >&2
  printf 'fatal: early EOF\\n' >&2
  exit 128
fi
exit 0
""",
        encoding="utf-8",
    )
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)

    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        "FETCH_COUNT_FILE": str(count_file),
        "FETCH_FAILS": str(failures),
        "FETCH_ERROR": error,
    })
    result = subprocess.run(
        ["bash", "-c", f"{_fetch_helper()}\ngit_fetch_with_retry\n"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    count = int(count_file.read_text(encoding="utf-8")) if count_file.exists() else 0
    return result, count


def test_vps_git_fetch_retries_transient_rpc_reset_then_succeeds(tmp_path):
    result, count = _run_helper(tmp_path, failures=2)

    assert result.returncode == 0
    assert count == 3
    assert "GIT_FETCH_SUCCESS attempt=3" in result.stdout
    assert result.stderr.count("reason=transient_transport") == 2
    assert "DEPLOY_ABORTED_GIT_FETCH_FAILED" not in result.stderr


def test_vps_git_fetch_stops_after_three_transient_failures(tmp_path):
    result, count = _run_helper(tmp_path, failures=3)

    assert result.returncode == 1
    assert count == 3
    assert "DEPLOY_ABORTED_GIT_FETCH_FAILED: merge not attempted" in result.stderr


def test_vps_git_fetch_does_not_retry_non_transient_authentication_error(tmp_path):
    result, count = _run_helper(tmp_path, failures=3, error="auth")

    assert result.returncode == 128
    assert count == 1
    assert "reason=non_retryable" in result.stderr
    assert "GIT_FETCH_ATTEMPT=2/3" not in result.stdout


def test_vps_git_fetch_must_succeed_before_fast_forward_merge():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert workflow.index("git_fetch_with_retry || exit 1") < workflow.index(
        "git merge --ff-only origin/main"
    )
