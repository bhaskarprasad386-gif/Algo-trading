"""Memory-conscious CSV exports for durable backtest results."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TextIO

from .ledger import BacktestLedger


def export_ledger_csv(
    ledger: BacktestLedger,
    run_id: str,
    destination: str | Path | TextIO,
    *,
    record_type: str | None = None,
) -> int:
    """Export ledger records as CSV and return the number of rows written."""
    records = ledger.records(run_id, record_type)
    payload_keys = sorted({key for record in records for key in record.payload})
    fieldnames = ["run_id", "record_type", "timestamp_ns", *payload_keys]

    close_after = False
    if hasattr(destination, "write"):
        handle = destination
    else:
        handle = open(destination, "w", newline="", encoding="utf-8")
        close_after = True
    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = {
                "run_id": record.run_id,
                "record_type": record.record_type,
                "timestamp_ns": record.timestamp_ns,
                **{
                    key: json.dumps(value, sort_keys=True, default=str)
                    if isinstance(value, (dict, list, tuple))
                    else value
                    for key, value in record.payload.items()
                },
            }
            writer.writerow(row)
    finally:
        if close_after:
            handle.close()
    return len(records)
