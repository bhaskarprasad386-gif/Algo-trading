from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "verify-live-cash-future-1s.yml"


def test_storage_diagnostic_remote_heredoc_is_closed_before_next_job():
    content = WORKFLOW.read_text(encoding="utf-8")
    storage_diagnostic = content.split("\n  verify:\n", 1)[0]

    assert len(re.findall(r"<<'REMOTE'", storage_diagnostic)) == 1
    assert len(re.findall(r"(?m)^\s*REMOTE\s*$", storage_diagnostic)) == 1
    assert len(re.findall(r"<<'PY'", storage_diagnostic)) == len(
        re.findall(r"(?m)^\s*PY\s*$", storage_diagnostic)
    )
