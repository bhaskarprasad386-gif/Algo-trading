from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


def test_iv_master_network_workflow_closes_python_heredoc():
    root = Path(__file__).resolve().parents[2]
    workflow_path = root / ".github" / "workflows" / "integration-tests.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    job = workflow["jobs"]["iv-independent-master-network"]
    step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Download and validate official master with production IV loader"
    )
    script = step["run"]
    lines = script.splitlines()

    assert lines[-1].strip() == "PY"
    assert sum(line.strip() == "PY" for line in lines) == 1

    result = subprocess.run(
        ["bash", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "delimited by end-of-file" not in result.stderr
