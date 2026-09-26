from datetime import date
from pathlib import Path

from app.backtesting.contract_master import ContractMasterCatalog
from app.scripts.import_historical_contract_snapshot_dir import main


def _write(path: Path, snapshot_date: str, token: str) -> None:
    path.write_text(
        f'''[
  {{
    "snapshot_date": "{snapshot_date}",
    "exchange": "NFO",
    "symbol": "ABC25OCTFUT",
    "token": "{token}",
    "expiry": "2025-10-30",
    "instrument_type": "STOCK_FUTURE",
    "underlying": "ABC",
    "lot_size": 100
  }}
]''',
        encoding="utf-8",
    )


def test_directory_import_uses_explicit_dates_from_filenames(tmp_path: Path, capsys) -> None:
    source = tmp_path / "snapshots"
    source.mkdir()
    _write(source / "2025-09-26.json", "9001", "2025-09-26")
    _write(source / "2025-09-29.json", "9002", "2025-09-29")
    db = tmp_path / "contracts.sqlite3"

    assert main(["--directory", str(source), "--contract-db", str(db)]) == 0

    output = capsys.readouterr().out
    assert "FILES=2" in output
    assert "COUNT=2" in output

    with ContractMasterCatalog(str(db)) as catalog:
        assert catalog.snapshot_dates() == (
            date(2025, 9, 26),
            date(2025, 9, 29),
        )


def test_directory_import_rejects_non_dated_filename(tmp_path: Path) -> None:
    source = tmp_path / "snapshots"
    source.mkdir()
    _write(source / "historical.json", "9001", "2025-09-26")

    try:
        main(["--directory", str(source), "--contract-db", str(tmp_path / "db.sqlite3")])
    except ValueError as exc:
        assert "YYYY-MM-DD.json" in str(exc)
    else:
        raise AssertionError("undated snapshot filename was accepted")
