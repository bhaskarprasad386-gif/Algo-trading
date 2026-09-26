from datetime import date
from pathlib import Path

from app.backtesting.contract_master import ContractMasterCatalog
from app.scripts.import_historical_contract_snapshot import main


def _write_snapshot(path: Path) -> None:
    path.write_text(
        """[
  {
    "snapshot_date": "2025-09-26",
    "exchange": "NFO",
    "symbol": "ABC25OCTFUT",
    "token": "9001",
    "expiry": "2025-10-30",
    "instrument_type": "STOCK_FUTURE",
    "underlying": "ABC",
    "lot_size": 100,
    "tick_size": 0.05
  }
]""",
        encoding="utf-8",
    )


def test_cli_imports_explicit_historical_snapshot(tmp_path: Path, capsys) -> None:
    source = tmp_path / "snapshot.json"
    db = tmp_path / "contracts.sqlite3"
    _write_snapshot(source)

    assert main(
        [
            "--file",
            str(source),
            "--source-date",
            "2025-09-26",
            "--contract-db",
            str(db),
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "HISTORICAL_CONTRACT_SNAPSHOT_STATUS=IMPORTED" in output
    assert "COUNT=1" in output

    with ContractMasterCatalog(str(db)) as catalog:
        assert catalog.snapshot_dates() == (date(2025, 9, 26),)
        assert len(catalog.all_contracts(snapshot_date=date(2025, 9, 26))) == 1


def test_cli_requires_explicit_source_date(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    db = tmp_path / "contracts.sqlite3"
    _write_snapshot(source)

    try:
        main(["--file", str(source), "--contract-db", str(db)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("CLI accepted a missing source date")
