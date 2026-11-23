from __future__ import annotations
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

class VpsHealthGateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.helper = self.root / "deploy" / "vps-health-gate.sh"
        self.temp = Path(tempfile.mkdtemp())
        self.count = self.temp / "curl-count"
        self.curl = self.temp / "mock-curl"
        self.sleep = self.temp / "mock-sleep"
        self.curl.write_text('''#!/bin/sh
n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
n=$((n+1))
printf '%s\\n' "$n" > "$COUNT_FILE"
if [ "$ALWAYS_FAIL" = "1" ] || [ "$n" -le "$FAIL_FIRST" ]; then exit 28; fi
exit 0
''', encoding="utf-8")
        self.sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.curl.chmod(0o755)
        self.sleep.chmod(0o755)
    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp, ignore_errors=True)
    def run_gate(self, *args, fail_first="0", always_fail="0"):
        env=os.environ.copy()
        env.update(HEALTH_GATE_CURL_BIN=str(self.curl), HEALTH_GATE_SLEEP_BIN=str(self.sleep), COUNT_FILE=str(self.count), FAIL_FIRST=fail_first, ALWAYS_FAIL=always_fail)
        return subprocess.run(["bash", str(self.helper), *args], env=env, capture_output=True, text=True, timeout=5)
    def test_success_on_first_attempt(self):
        r=self.run_gate("http://127.0.0.1:8000/health", "HEALTH", "2", "8", "0")
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn("HEALTH=PASS",r.stdout)
        self.assertEqual(self.count.read_text().strip(),"1")
    def test_retries_then_success(self):
        r=self.run_gate("http://127.0.0.1:8000/health","HEALTH","3","8","0",fail_first="1")
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn("HEALTH_RETRY_ATTEMPT=1",r.stderr)
        self.assertEqual(self.count.read_text().strip(),"2")
    def test_failed_health_never_returns_success_after_exhaustion(self):
        r=self.run_gate("http://127.0.0.1:8000/health","HEALTH","3","8","0",always_fail="1")
        self.assertEqual(r.returncode,1)
        self.assertIn("HEALTH=FAIL after 3 attempts",r.stderr)
        self.assertEqual(self.count.read_text().strip(),"3")
    def test_workflow_gates_health_and_live_data(self):
        content=(self.root/".github/workflows/vps-deploy.yml").read_text(encoding="utf-8")
        for url,label in (("http://127.0.0.1:8000/health","HEALTH"),("http://127.0.0.1:8000/api/v1/market-data/live-health","LIVE_DATA_HEALTH")):
            needle='if ! bash "$APP_DIR/deploy/vps-health-gate.sh" "'+url+'" "'+label+'" 12 8 2; then'
            self.assertIn(needle,content)
if __name__ == "__main__":
    unittest.main()
