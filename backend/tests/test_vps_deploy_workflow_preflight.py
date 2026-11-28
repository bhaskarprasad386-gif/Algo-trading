"""Regression guards for the VPS deploy SSH preflight timeout and fail-closed gate."""

import re
from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "vps-deploy.yml"


def test_vps_ssh_preflight_timeout_has_slow_session_buffer():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    match = re.search(r"timeout\s+(\d+)s\s+ssh\s+-vvv\s+-T", workflow)
    assert match, "SSH preflight must be explicitly bounded"
    assert int(match.group(1)) >= 60, (
        "SSH preflight needs a buffer for slow key exchange/authentication; "
        "35 seconds expired before the remote echo returned in recent deploy logs"
    )


def test_vps_deploy_fails_closed_before_remote_command_when_preflight_fails():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    failure_marker = workflow.index("SSH_AUTH_PREFLIGHT_FAILED: deployment was not started")
    remote_launch = workflow.index("REMOTE_DEPLOY_LAUNCH_SYNCHRONOUS")
    failure_block = workflow[workflow.index("SSH_AUTH_PREFLIGHT_START") : remote_launch]
    assert failure_marker < remote_launch
    assert "exit 1" in failure_block
