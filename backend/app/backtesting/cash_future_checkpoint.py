"""Safe serializable checkpoint state for Cash-Future strategy runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class CashFutureCheckpointState:
    """Runner-owned state that is safe to persist and restore.

    Strategy-specific mutable state is intentionally excluded. Strategies must
    provide their own explicit serializable state contract before it can be
    included in a resume checkpoint.
    """

    event_index: int
    timestamp: datetime
    selected_contract: str | None
    realized_capital: float
    reserved_margin: float
    blocked_entries: int
    entry: Mapping[str, Any] | None
    strategy_id: str
    strategy_version: str
    strategy_hash: str | None = None
    data_source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.event_index < 0:
            raise ValueError("event_index must be non-negative")
        if self.realized_capital <= 0:
            raise ValueError("realized_capital must be positive")
        if self.reserved_margin < 0:
            raise ValueError("reserved_margin cannot be negative")
        if self.blocked_entries < 0:
            raise ValueError("blocked_entries cannot be negative")
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("strategy identifiers are required")

    def to_mapping(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload

    @classmethod
    def from_mapping(cls, state: Mapping[str, Any]) -> "CashFutureCheckpointState":
        required = {
            "event_index", "timestamp", "selected_contract", "realized_capital",
            "reserved_margin", "blocked_entries", "entry", "strategy_id", "strategy_version",
        }
        missing = sorted(required - set(state))
        if missing:
            raise ValueError(f"checkpoint state missing fields: {', '.join(missing)}")
        timestamp = state["timestamp"]
        if not isinstance(timestamp, datetime):
            timestamp = datetime.fromisoformat(str(timestamp))
        return cls(
            event_index=int(state["event_index"]),
            timestamp=timestamp,
            selected_contract=state["selected_contract"],
            realized_capital=float(state["realized_capital"]),
            reserved_margin=float(state["reserved_margin"]),
            blocked_entries=int(state["blocked_entries"]),
            entry=state["entry"],
            strategy_id=str(state["strategy_id"]),
            strategy_version=str(state["strategy_version"]),
            strategy_hash=state.get("strategy_hash"),
            data_source_fingerprint=state.get("data_source_fingerprint"),
        )


__all__ = ["CashFutureCheckpointState"]
