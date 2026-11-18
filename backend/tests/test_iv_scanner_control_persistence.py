from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_iv_scanner_control_persistence_failure_is_fail_closed(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    code = r"""
from fastapi import HTTPException
from app import main

events = []
def save(enabled):
    events.append(("save", enabled))
    if enabled is True and SAVE_FAIL_ON:
        raise RuntimeError("database unavailable")
    if enabled is False and SAVE_FAIL_OFF:
        raise RuntimeError("database unavailable")
    return enabled

def start():
    events.append(("start",))
    if START_FAIL:
        raise RuntimeError("runner startup failed")
    return {"status": "RUNNING"}

def stop():
    events.append(("stop",))
    return {"status": "STOPPED"}

main._save_iv_scanner_enabled = save
main._start_iv_skew_flip_runtime = start
main._stop_iv_skew_flip_runtime = stop

# ON persistence failure must not start the runner.
SAVE_FAIL_ON = True
SAVE_FAIL_OFF = False
START_FAIL = False
try:
    main.save_iv_skew_flip_config({"enabled": True})
except HTTPException as exc:
    assert exc.status_code == 503
else:
    raise AssertionError("ON persistence failure must return HTTP 503")
assert events == [("save", True)]
events.clear()

# OFF persistence failure must not stop an already running runner.
SAVE_FAIL_ON = False
SAVE_FAIL_OFF = True
try:
    main.save_iv_skew_flip_config({"enabled": False})
except HTTPException as exc:
    assert exc.status_code == 503
else:
    raise AssertionError("OFF persistence failure must return HTTP 503")
assert events == [("save", False)]
events.clear()

# Failed startup must compensate the persisted ON state back to OFF.
SAVE_FAIL_ON = False
SAVE_FAIL_OFF = False
START_FAIL = True
try:
    main.save_iv_skew_flip_config({"enabled": True})
except HTTPException as exc:
    assert exc.status_code == 503
else:
    raise AssertionError("runner startup failure must return HTTP 503")
assert events == [("save", True), ("start",), ("save", False)]
events.clear()

# Successful ON/OFF transitions persist desired state before runtime changes.
START_FAIL = False
on_result = main.save_iv_skew_flip_config({"enabled": True})
assert on_result["saved_enabled"] is True
assert events == [("save", True), ("start",)]
events.clear()
off_result = main.save_iv_skew_flip_config({"enabled": False})
assert off_result["saved_enabled"] is False
assert events == [("save", False), ("stop",)]
print("IV control persistence failure scenarios passed")
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
            "CASH_FUTURE_HISTORY_ENABLED": "false",
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
        f"IV control persistence subprocess failed: "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    assert "IV control persistence failure scenarios passed" in completed.stdout
