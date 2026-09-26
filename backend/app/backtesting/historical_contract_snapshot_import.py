"""Strict import boundary for genuine historical futures contract snapshots.

This module only imports already-dated source records. It never derives or
renames a current contract-master payload into a historical snapshot.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contract_master import ContractMasterCatalog, ContractRecord


REQUIRED_FIELDS = (
    "snapshot_date",
    "exchange",
    "symbol",
    "token",
    "expiry",
    "instrument_type",
    "underlying",
    "lot_size",
)


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be ISO YYYY-MM-DD") from None


def parse_historical_contract_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_date: date,
) -> tuple[ContractRecord, ...]:
    """Validate normalized historical rows without inventing missing metadata.

    source_date is the date asserted by the external historical source.
    Every row must carry the same explicit snapshot_date; callers cannot use
    today's date as an implicit historical date.
    """
    if not isinstance(source_date, date):
        raise TypeError("source_date must be a date")
    if not rows:
        raise ValueError("historical contract source is empty")

    records: list[ContractRecord] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"row {index} must be an object")
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"row {index} missing required fields: {', '.join(missing)}")

        snapshot_date = _date(row["snapshot_date"], "snapshot_date")
        if snapshot_date != source_date:
            raise ValueError(
                f"row {index} snapshot_date {snapshot_date.isoformat()} "
                f"does not match source_date {source_date.isoformat()}"
            )

        records.append(
            ContractRecord(
                exchange=str(row["exchange"]).strip().upper(),
                symbol=str(row["symbol"]).strip(),
                token=str(row["token"]).strip(),
                expiry=_date(row["expiry"], "expiry"),
                instrument_type=str(row["instrument_type"]).strip().upper(),
                underlying=str(row["underlying"]).strip().upper(),
                lot_size=int(row["lot_size"]),
                snapshot_date=snapshot_date,
                tick_size=(
                    None
                    if row.get("tick_size") is None
                    else float(row["tick_size"])
                ),
            )
        )

    identities = [(record.exchange, record.token) for record in records]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate contract identity (exchange, token) in historical source")
    return tuple(records)


def import_historical_contract_snapshot(
    catalog: ContractMasterCatalog,
    rows: Sequence[Mapping[str, Any]],
    *,
    source_date: date,
    payload: bytes | None = None,
) -> int:
    """Persist one externally sourced, explicitly dated historical snapshot."""
    records = parse_historical_contract_records(rows, source_date=source_date)
    digest = None if payload is None else hashlib.sha256(payload).hexdigest()
    return catalog.upsert_snapshot(
        source_date,
        records,
        payload_sha256=digest,
    )


def load_json_snapshot(path: str | Path, *, source_date: date) -> list[Mapping[str, Any]]:
    """Load a normalized JSON array; no date or contract data is synthesized."""
    payload = Path(path).read_bytes()
    decoded = json.loads(payload)
    if not isinstance(decoded, list):
        raise ValueError("historical contract JSON must contain an array")
    if not all(isinstance(row, Mapping) for row in decoded):
        raise ValueError("historical contract JSON rows must be objects")
    return list(decoded)


__all__ = [
    "import_historical_contract_snapshot",
    "load_json_snapshot",
    "parse_historical_contract_records",
]
