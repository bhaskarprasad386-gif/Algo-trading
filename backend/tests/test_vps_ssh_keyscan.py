from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class VpsSshKeyscanDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.helper = self.root / "deploy" / "vps-ssh-keyscan.sh"
        self.temp = Path(tempfile.mkdtemp())
        self.bin = self.temp / "bin"
        self.bin.mkdir()
        self.evidence = self.temp / "vps-health-probe.txt"
        self.known_hosts = self.temp / ".ssh" / "known_hosts"
        self.count_file = self.temp / "keyscan-count"

        self.timeout = self._write_executable(
            "timeout",
            "#!/bin/sh\nshift\nexec \"$@\"\n",
        )
        self.sleep = self._write_executable("sleep", "#!/bin/sh\nexit 0\n")
        self.python = self._write_executable(
            "python3",
            "#!/bin/sh\nprintf '%s\\n' \"$TCP_PROBE_OUTPUT\"\nexit 0\n",
        )
        self.keyscan = self._write_executable(
            "ssh-keyscan",
            """#!/bin/sh
n=$(cat "$KEYSCAN_COUNT_FILE" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\\n' "$n" > "$KEYSCAN_COUNT_FILE"
case "$KEYSCAN_MODE" in
  success_after_retry)
    if [ "$n" -eq 1 ]; then
      printf 'temporary keyscan failure\\n' >&2
      exit 1
    fi
    printf '%s ssh-ed25519 AAAATESTKEY\\n' "$VPS_HOST"
    exit 0
    ;;
  empty_failure)
    exit 1
    ;;
  timeout)
    exit 124
    ;;
  *)
    printf 'unexpected test mode\\n' >&2
    exit 2
    ;;
esac
""",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _write_executable(self, name: str, content: str) -> Path:
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def run_helper(self, mode: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "VPS_HOST": "82.41.67.22",
                "VPS_SSH_PORT": "22",
                "VPS_HEALTH_EVIDENCE_FILE": str(self.evidence),
                "VPS_KNOWN_HOSTS_FILE": str(self.known_hosts),
                "VPS_KEYSCAN_BIN": str(self.keyscan),
                "VPS_TIMEOUT_BIN": str(self.timeout),
                "VPS_SLEEP_BIN": str(self.sleep),
                "VPS_PYTHON_BIN": str(self.python),
                "VPS_KEYSCAN_ATTEMPTS": "3",
                "VPS_KEYSCAN_COMMAND_TIMEOUT_SECONDS": "8",
                "VPS_KEYSCAN_CONNECT_TIMEOUT_SECONDS": "5",
                "VPS_KEYSCAN_RETRY_DELAY_SECONDS": "0",
                "VPS_TCP_PROBE_TIMEOUT_SECONDS": "1",
                "KEYSCAN_COUNT_FILE": str(self.count_file),
                "KEYSCAN_MODE": mode,
                "TCP_PROBE_OUTPUT": (
                    "VPS_SSH_TCP_CONNECT=CONNECTION_REFUSED "
                    "errno=111 error=ConnectionRefusedError"
                ),
            }
        )
        return subprocess.run(
            ["bash", str(self.helper)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_success_after_retry_publishes_host_key(self) -> None:
        result = self.run_helper("success_after_retry")
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.evidence.read_text(encoding="utf-8")
        self.assertIn("SSH_KEYSCAN_ATTEMPT=1 EXIT_CODE=1", evidence)
        self.assertIn("SSH_KEYSCAN_ATTEMPT=2 EXIT_CODE=0", evidence)
        self.assertIn("VPS_SSH_KEYSCAN=OK attempt=2", evidence)
        self.assertIn("AAAATESTKEY", self.known_hosts.read_text(encoding="utf-8"))
        self.assertNotIn("AAAATESTKEY", evidence)

    def test_empty_stderr_failure_still_creates_uploadable_evidence(self) -> None:
        result = self.run_helper("empty_failure")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.evidence.is_file())
        self.assertGreater(self.evidence.stat().st_size, 0)
        evidence = self.evidence.read_text(encoding="utf-8")
        self.assertIn("VPS_SSH_KEYSCAN=FAILED attempts=3", evidence)
        self.assertIn("STDOUT_BYTES=0 STDERR_BYTES=0", evidence)
        self.assertIn("last_failure=NONZERO_EXIT_NO_HOST_KEY", evidence)
        self.assertIn("VPS_SSH_TCP_CONNECT=CONNECTION_REFUSED", evidence)
        self.assertEqual(self.count_file.read_text().strip(), "3")

    def test_timeout_is_classified_and_evidence_is_preserved(self) -> None:
        result = self.run_helper("timeout")
        self.assertEqual(result.returncode, 1)
        evidence = self.evidence.read_text(encoding="utf-8")
        self.assertIn("SSH_KEYSCAN_ATTEMPT_FAILURE_KIND=TIMEOUT", evidence)
        self.assertIn("last_failure=TIMEOUT", evidence)
        self.assertIn("VPS_SSH_TCP_CONNECT=CONNECTION_REFUSED", evidence)

    def test_workflow_checks_out_helper_and_appends_remote_evidence(self) -> None:
        workflow = (
            self.root / ".github" / "workflows" / "vps-health-probe.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("uses: actions/checkout@v4", workflow)
        self.assertIn("if ! bash deploy/vps-ssh-keyscan.sh; then", workflow)
        self.assertIn("tee -a /tmp/vps-health-probe.txt", workflow)
        self.assertIn("path: /tmp/vps-health-probe.txt", workflow)


if __name__ == "__main__":
    unittest.main()
