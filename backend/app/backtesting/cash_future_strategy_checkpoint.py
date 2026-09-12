"""Serializable checkpoint state for resumable Cash-Future strategy runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from typing import Any, Mapping


@dataclass(frozen=True)
class CashFutureStrategyCheckpoint:
    """JSON-serializable execution state owned by the runner, not the strategy."""

    run_id: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str | None
    last_timestamp: str
    selected_contract: str | None
    realized_capital: float
    reserved_margin: float
    blocked_entries: int
    open_entry: Mapping[str, Any] | None
    source_fingerprint: str | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "CashFutureStrategyCheckpoint":
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("checkpoint payload must be an object")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CashFutureStrategyCheckpoint":
        required = {
            "run_id",
            "strategy_id",
            "strategy_version",
            "last_timestamp",
            "selected_contract",
            "realized_capital",
            "reserved_margin",
            "blocked_entries",
            "open_entry",
            "strategy_hash",
            "source_fingerprint",
        }
        missing = required.difference(payload)
        if missing:
            raise ValueError(f"checkpoint missing fields: {sorted(missing)}")
        if not isinstance(payload["run_id"], str) or not payload["run_id"].strip():
            raise ValueError("checkpoint run_id is required")
        if not isinstance(payload["strategy_id"], str) or not payload["strategy_id"].strip():
            raise ValueError("checkpoint strategy_id is required")
        if not isinstance(payload["strategy_version"], str) or not payload["strategy_version"].strip():
            raise ValueError("checkpoint strategy_version is required")
        try:
            datetime.fromisoformat(str(payload["last_timestamp"]))
        except (TypeError, ValueError) as exc:
            try:
                date.fromisoformat(str(payload["last_timestamp"]))
            except (TypeError, ValueError):
                raise ValueError("checkpoint last_timestamp must be ISO date/datetime") from exc
        return cls(
            run_id=payload["run_id"],
            strategy_id=payload["strategy_id"],
            strategy_version=payload["strategy_version"],
            strategy_hash=payload["strategy_hash"],
            last_timestamp=payload["last_timestamp"],
            selected_contract=payload["selected_contract"],
            realized_capital=float(payload["realized_capital"]),
            reserved_margin=float(payload["reserved_margin"]),
            blocked_entries=int(payload["blocked_entries"]),
            open_entry=payload["open_entry"],
            source_fingerprint=payload["source_fingerprint"],
        )


__all__ = ["CashFutureStrategyCheckpoint"]
