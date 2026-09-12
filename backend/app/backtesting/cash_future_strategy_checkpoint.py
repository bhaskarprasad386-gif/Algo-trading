"""Serializable checkpoint state for resumable Cash-Future strategy runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from typing import Any, Mapping


@dataclass(frozen=True)
class CashFutureStrategyCheckpoint:
    """JSON-serializable runner state plus explicitly supplied strategy state."""

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
    strategy_state: Mapping[str, Any] | None = None

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
            "run_id", "strategy_id", "strategy_version", "last_timestamp",
            "selected_contract", "realized_capital", "reserved_margin",
            "blocked_entries", "open_entry", "strategy_hash", "source_fingerprint",
        }
        missing = required.difference(payload)
        if missing:
            raise ValueError(f"checkpoint missing fields: {sorted(missing)}")
        for field in ("run_id", "strategy_id", "strategy_version"):
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise ValueError(f"checkpoint {field} is required")
        try:
            datetime.fromisoformat(str(payload["last_timestamp"]))
        except (TypeError, ValueError) as exc:
            try:
                date.fromisoformat(str(payload["last_timestamp"]))
            except (TypeError, ValueError):
                raise ValueError("checkpoint last_timestamp must be ISO date/datetime") from exc
        strategy_state = payload.get("strategy_state")
        if strategy_state is not None:
            if not isinstance(strategy_state, Mapping):
                raise ValueError("checkpoint strategy_state must be an object")
            try:
                json.dumps(dict(strategy_state), sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise ValueError("checkpoint strategy_state must be JSON-serializable") from exc
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
            strategy_state=dict(strategy_state) if strategy_state is not None else None,
        )


def validate_cash_future_checkpoint(
    checkpoint: CashFutureStrategyCheckpoint,
    *,
    run_id: str,
    strategy_id: str,
    strategy_version: str,
    strategy_hash: str | None,
    data_source_fingerprint: str | None,
) -> None:
    """Reject a checkpoint when its execution identity differs from the resume request."""
    checks = (
        ("run_id", checkpoint.run_id, run_id),
        ("strategy_id", checkpoint.strategy_id, strategy_id),
        ("strategy_version", checkpoint.strategy_version, strategy_version),
        ("strategy_hash", checkpoint.strategy_hash, strategy_hash),
        ("source_fingerprint", checkpoint.source_fingerprint, data_source_fingerprint),
    )
    for name, stored, requested in checks:
        if stored != requested:
            raise ValueError(
                f"unsafe Cash-Future resume: {name} mismatch "
                f"(stored={stored!r}, requested={requested!r})"
            )


__all__ = ["CashFutureStrategyCheckpoint", "validate_cash_future_checkpoint"]
