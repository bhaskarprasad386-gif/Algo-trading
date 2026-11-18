#!/usr/bin/env python3
"""Import a directory of explicitly dated historical contract snapshots."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from app.backtesting.contract_master import ContractMasterCatalog
from app.backtesting.historical_contract_snapshot_import import (
    import_historical_contract_snapshot,
    load_json_snapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import genuine historical contract-master JSON snapshots from a directory."
    )
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--contract-db", required=True, type=Path)
    return parser


def _source_date(path: Path) -> date:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        raise ValueError(
            f"snapshot filename must be YYYY-MM-DD.json: {path.name}"
        ) from None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = sorted(args.directory.glob("*.json"))
    if not paths:
        raise ValueError("historical contract snapshot directory is empty")

    imported = 0
    with ContractMasterCatalog(str(args.contract_db)) as catalog:
        for path in paths:
            source_date = _source_date(path)
            payload = path.read_bytes()
            rows = load_json_snapshot(path, source_date=source_date)
            imported += import_historical_contract_snapshot(
                catalog,
                rows,
                source_date=source_date,
                payload=payload,
            )

    print(f"HISTORICAL_CONTRACT_SNAPSHOT_BATCH_STATUS=IMPORTED FILES={len(paths)} COUNT={imported}")
    print(f"HISTORICAL_CONTRACT_SNAPSHOT_BATCH_DB={args.contract_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
