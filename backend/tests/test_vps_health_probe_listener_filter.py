from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


class VpsHealthProbeListenerFilterTests(unittest.TestCase):
    def test_workflow_listener_filter_matches_expected_ports_only(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github" / "workflows" / "vps-health-probe.yml").read_text(
            encoding="utf-8"
        )
        listener_line = next(
            line for line in workflow.splitlines() if "ss -ltn" in line
        )
        match = re.search(r"grep -E '([^']+)'", listener_line)
        self.assertIsNotNone(match, listener_line)
        pattern = match.group(1)

        sample_ss_output = "\n".join(
            [
                "State Recv-Q Send-Q Local Address:Port Peer Address:Port",
                "LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*",
                "LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*",
                "LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*",
                "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*",
                "LISTEN 0 4096 127.0.0.1:8443 0.0.0.0:*",
                "LISTEN 0 4096 127.0.0.1:18000 0.0.0.0:*",
            ]
        )
        result = subprocess.run(
            ["grep", "-E", pattern],
            input=sample_ss_output,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*",
                "LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*",
                "LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*",
                "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*",
            ],
        )


if __name__ == "__main__":
    unittest.main()
