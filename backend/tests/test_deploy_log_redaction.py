import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REDACTOR = REPO_ROOT / "deploy" / "redact-sensitive-logs.py"


def test_journal_redactor_removes_credentials_before_ci_output():
    source = (
        "SDK Headers: {'X-PrivateKey': 'journal-private', "
        "'Authorization': 'Bearer journal-jwt'} Request: "
        "{'password': 'journal-pin', 'refreshToken': 'journal-refresh'}\n"
        "Service health HTTP=200 client_ip=127.0.0.1\n"
    )
    result = subprocess.run(
        [sys.executable, str(REDACTOR)],
        input=source,
        capture_output=True,
        text=True,
        check=True,
    )
    for secret in ("journal-private", "journal-jwt", "journal-pin", "journal-refresh"):
        assert secret not in result.stdout
    assert result.stdout.count("[REDACTED]") >= 4
    assert "HTTP=200" in result.stdout
    assert "127.0.0.1" in result.stdout
