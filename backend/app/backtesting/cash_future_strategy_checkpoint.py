"""Serializable checkpoint state for resumable Cash-Future strategy runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
import math
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
        payload = asdict(self)
        _validate_payload_values(payload)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, value: str) -> "CashFutureStrategyCheckpoint":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint payload must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("checkpoint payload must be an object")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CashFutureStrategyCheckpoint":
        if not isinstance(payload, Mapping):
            raise ValueError("checkpoint payload must be an object")
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
        for field in ("strategy_hash", "source_fingerprint"):
            if payload[field] is not None and not isinstance(payload[field], str):
                raise ValueError(f"checkpoint {field} must be a string or null")
        if payload["selected_contract"] is not None and not isinstance(payload["selected_contract"], str):
            raise ValueError("checkpoint selected_contract must be a string or null")
        try:
            datetime.fromisoformat(str(payload["last_timestamp"]))
        except (TypeError, ValueError) as exc:
            try:
                date.fromisoformat(str(payload["last_timestamp"]))
            except (TypeError, ValueError):
                raise ValueError("checkpoint last_timestamp must be ISO date/datetime") from exc
        _validate_number(payload["realized_capital"], "realized_capital")
        _validate_number(payload["reserved_margin"], "reserved_margin")
        if not isinstance(payload["blocked_entries"], int) or isinstance(payload["blocked_entries"], bool):
            raise ValueError("checkpoint blocked_entries must be an integer")
        if payload["blocked_entries"] < 0:
            raise ValueError("checkpoint blocked_entries cannot be negative")

        open_entry = payload.get("open_entry")
        if open_entry is not None:
            if not isinstance(open_entry, Mapping):
                raise ValueError("checkpoint open_entry must be an object or null")
            try:
                json.dumps(dict(open_entry), sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("checkpoint open_entry must be JSON-serializable") from exc

        strategy_state = payload.get("strategy_state")
        if strategy_state is not None:
            if not isinstance(strategy_state, Mapping):
                raise ValueError("checkpoint strategy_state must be an object")
            try:
                json.dumps(dict(strategy_state), sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("checkpoint strategy_state must be JSON-serializable") from exc

        checkpoint = cls(
            run_id=payload["run_id"],
            strategy_id=payload["strategy_id"],
            strategy_version=payload["strategy_version"],
            strategy_hash=payload["strategy_hash"],
            last_timestamp=payload["last_timestamp"],
            selected_contract=payload["selected_contract"],
            realized_capital=float(payload["realized_capital"]),
            reserved_margin=float(payload["reserved_margin"]),
            blocked_entries=payload["blocked_entries"],
            open_entry=dict(open_entry) if open_entry is not None else None,
            source_fingerprint=payload["source_fingerprint"],
            strategy_state=dict(strategy_state) if strategy_state is not None else None,
        )
        _validate_payload_values(asdict(checkpoint))
        return checkpoint


def _validate_number(value: Any, field: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"checkpoint {field} must be a finite number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"checkpoint {field} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"checkpoint {field} must be a finite number")


def _validate_payload_values(payload: Mapping[str, Any]) -> None:
    _validate_number(payload["realized_capital"], "realized_capital")
    _validate_number(payload["reserved_margin"], "reserved_margin")
    if payload["blocked_entries"] < 0:
        raise ValueError("checkpoint blocked_entries cannot be negative")
    if payload.get("open_entry") is not None:
        if not isinstance(payload["open_entry"], Mapping):
            raise ValueError("checkpoint open_entry must be an object or null")
        try:
            json.dumps(dict(payload["open_entry"]), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint open_entry must be JSON-serializable") from exc
    if payload.get("strategy_state") is not None:
        if not isinstance(payload["strategy_state"], Mapping):
            raise ValueError("checkpoint strategy_state must be an object")
        try:
            json.dumps(dict(payload["strategy_state"]), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint strategy_state must be JSON-serializable") from exc


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
