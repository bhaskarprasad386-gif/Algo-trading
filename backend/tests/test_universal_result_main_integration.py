from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_main_app_uses_isolated_universal_result_ledger(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    code = r"""
import json

from fastapi.testclient import TestClient

from app import main
from app.backtesting.result_ledger import BacktestEvent


run_id = "main-integration"
main.universal_result_ledger.create_run(
    run_id,
    {"initial_capital": 100_000.0, "integration": "main-app"},
)
main.universal_result_ledger.append_events(
    run_id,
    [BacktestEvent(0, 1_000, "EVENT", {"source": "main"})],
)
main.universal_result_ledger.set_status(run_id, "COMPLETED")

with TestClient(main.app) as client:
    summary = client.get(
        f"/api/v1/backtesting/universal/runs/{run_id}"
    )
    events = client.get(
        f"/api/v1/backtesting/universal/runs/{run_id}/events?limit=1"
    )
    missing = client.get(
        "/api/v1/backtesting/universal/runs/does-not-exist"
    )

    assert summary.status_code == 200
    assert summary.json()["run_id"] == run_id
    assert events.status_code == 200
    assert events.json()["count"] == 1
    assert events.json()["data"][0]["payload_json"] == '{"source":"main"}'
    assert missing.status_code == 404

print(json.dumps({"status": "ok"}))
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
        f"main.app integration subprocess failed: "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    assert output_lines
    assert json.loads(output_lines[-1]) == {"status": "ok"}
