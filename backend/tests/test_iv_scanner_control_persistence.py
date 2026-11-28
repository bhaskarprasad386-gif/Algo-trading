from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_iv_manual_control_endpoint_is_retired_without_side_effects(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    code = r"""
from fastapi import HTTPException
from app import main

# Manual ON/OFF API is intentionally retired. It must fail closed and must
# not touch persistence or runtime lifecycle functions.
events = []
main._save_iv_scanner_enabled = lambda enabled: events.append(("save", enabled))
main._start_iv_skew_flip_runtime = lambda: events.append(("start",))
main._stop_iv_skew_flip_runtime = lambda: events.append(("stop",))

try:
    main.save_iv_skew_flip_config({"enabled": True})
except HTTPException as exc:
    assert exc.status_code == 410
    assert "automatic mode" in exc.detail.lower()
else:
    raise AssertionError("manual IV configuration endpoint must be retired")

assert events == []
print("IV manual controls retirement passed")
"""
    env = os.environ.copy()
    env.update(
        {
            "SECRET_KEY": "ci-test-secret-key-not-for-production",
            "PYTHONPATH": str(backend_dir),
            "DATABASE_URL": f"sqlite:///{tmp_path / 'app.sqlite3'}",
            "BACKTEST_STATUS_DB": str(tmp_path / "status.sqlite3"),
            "BACKTEST_DATA_DB": str(tmp_path / "market_data.sqlite3"),
            "BACKTEST_CONTRACT_DB": str(tmp_path / "contract.sqlite3"),
            "BACKTEST_RESULT_LEDGER_DB": str(tmp_path / "result.sqlite3"),
            "BACKTEST_CONTRACT_MASTER_AUTO_SYNC": "false",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_dir.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"IV manual-control retirement subprocess failed: "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    assert "IV manual controls retirement passed" in completed.stdout
