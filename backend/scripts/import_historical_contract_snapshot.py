#!/usr/bin/env python3
"""Import an externally supplied, explicitly dated historical contract snapshot."""

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
        description="Import a genuine historical contract-master JSON snapshot."
    )
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--source-date", required=True, type=date.fromisoformat)
    parser.add_argument("--contract-db", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = args.file.read_bytes()
    rows = load_json_snapshot(args.file, source_date=args.source_date)

    with ContractMasterCatalog(str(args.contract_db)) as catalog:
        count = import_historical_contract_snapshot(
            catalog,
            rows,
            source_date=args.source_date,
            payload=payload,
        )
        stored = catalog.all_contracts(snapshot_date=args.source_date)

    print(
        f"HISTORICAL_CONTRACT_SNAPSHOT_STATUS=IMPORTED "
        f"DATE={args.source_date.isoformat()} COUNT={count}"
    )
    print(f"HISTORICAL_CONTRACT_SNAPSHOT_DB={args.contract_db}")
    print(f"HISTORICAL_CONTRACT_SNAPSHOT_STORED={len(stored)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
