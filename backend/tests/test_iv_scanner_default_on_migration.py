from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_legacy_saved_off_is_migrated_to_default_on_once(tmp_path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    code = r"""
from datetime import datetime, timezone
from app import main
from app.models.iv_scanner_control import IVScannerControl, IVScannerControlMigration

with main.SessionLocal() as db:
    db.add(IVScannerControl(id=1, enabled=False, updated_at=datetime.now(timezone.utc)))
    db.commit()

# The first read performs the one-time rollout migration.
assert main._get_iv_scanner_enabled() is True
with main.SessionLocal() as db:
    setting = db.get(IVScannerControl, 1)
    marker = db.query(IVScannerControlMigration).filter_by(
        migration_key="iv_scanner_default_on_2026_11"
    ).one()
    assert setting.enabled is True
    assert marker.id == 1

# A later explicit operator OFF must remain OFF, including on subsequent reads.
assert main._save_iv_scanner_enabled(False) is False
assert main._get_iv_scanner_enabled() is False
print("IV default-ON migration and explicit OFF preservation passed")
"""
    env = os.environ.copy()
    env.update({
        "SECRET_KEY": "ci-test-secret-key-not-for-production",
        "PYTHONPATH": str(backend_dir),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'iv-control.sqlite3'}",
        "BACKTEST_STATUS_DB": str(tmp_path / "status.sqlite3"),
        "BACKTEST_DATA_DB": str(tmp_path / "market_data.sqlite3"),
        "BACKTEST_CONTRACT_DB": str(tmp_path / "contract.sqlite3"),
        "BACKTEST_RESULT_LEDGER_DB": str(tmp_path / "result.sqlite3"),
        "BACKTEST_CONTRACT_MASTER_AUTO_SYNC": "false",
    })
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_dir.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"IV default migration subprocess failed: "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    assert "IV default-ON migration and explicit OFF preservation passed" in completed.stdout
