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
import time

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

    # The deployment readiness endpoint must fail closed when the DB probe
    # fails, rather than returning HTTP 200 with a degraded JSON body.
    original_check_database = main.check_database
    try:
        main.check_database = lambda: (_ for _ in ()).throw(RuntimeError("db unavailable"))
        degraded = client.get("/health")
    finally:
        main.check_database = original_check_database

    assert summary.status_code == 200
    assert summary.json()["run_id"] == run_id
    assert events.status_code == 200
    assert events.json()["count"] == 1
    assert events.json()["data"][0]["payload_json"] == '{"source":"main"}'
    assert missing.status_code == 404
    assert degraded.status_code == 503
    assert degraded.json()["status"] == "degraded"
    assert degraded.json()["database"] == "Disconnected"

    # A still-running timed-out worker owns the only probe slot. Concurrent
    # probes must fail quickly instead of spawning more blocked worker threads.
    assert main._health_db_probe_semaphore.acquire(blocking=False)
    try:
        started = time.monotonic()
        busy = client.get("/health")
        busy_elapsed = time.monotonic() - started
    finally:
        main._health_db_probe_semaphore.release()

    assert busy.status_code == 503
    assert busy.json()["database"] == "ProbeBusy"
    assert busy_elapsed < 0.5, f"busy health probe was not rejected promptly: {busy_elapsed:.2f}s"
    busy_events = main.runtime_diagnostics.snapshot(limit=5)
    assert any(
        event.get("error_type") == "DatabaseProbeBusy"
        and event.get("endpoint") == "/health"
        for event in busy_events
    )


    # A database call that never returns must not pin the ASGI health request.
    original_check_database = main.check_database
    try:
        def slow_database_probe():
            time.sleep(3.5)
            return True
        main.check_database = slow_database_probe
        started = time.monotonic()
        timed_out = client.get("/health")
        elapsed = time.monotonic() - started
    finally:
        main.check_database = original_check_database

    assert timed_out.status_code == 503
    assert timed_out.json()["status"] == "degraded"
    assert elapsed < 3.4, f"health timeout exceeded bound: {elapsed:.2f}s"
    timeout_events = main.runtime_diagnostics.snapshot(limit=5)
    assert any(
        event.get("error_type") == "DatabaseProbeTimeout"
        and event.get("endpoint") == "/health"
        for event in timeout_events
    )
print(json.dumps({"status": "ok"}))
"""

    env = os.environ.copy()
    env.update(
        {
            "SECRET_KEY": "ci-test-secret-key-not-for-production",
            "BACKTESTING_ENABLED": "true",
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
