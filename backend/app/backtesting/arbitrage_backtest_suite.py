"""First-class registry for the four historical arbitrage strategy families."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Mapping

from .provenance import provenance_hash
from .arbitrage_strategy_adapters import (
    BoxSpreadStrategyAdapter,
    CalendarSpreadStrategyAdapter,
    CashFutureStrategyAdapter,
    SyntheticCashCarryStrategyAdapter,
)


@dataclass(frozen=True)
class ArbitrageStrategyDefinition:
    strategy_id: str
    adapter_type: type


STRATEGIES: dict[str, ArbitrageStrategyDefinition] = {
    "box-spread": ArbitrageStrategyDefinition("box-spread", BoxSpreadStrategyAdapter),
    "synthetic-cash-carry": ArbitrageStrategyDefinition(
        "synthetic-cash-carry", SyntheticCashCarryStrategyAdapter
    ),
    "cash-future": ArbitrageStrategyDefinition("cash-future", CashFutureStrategyAdapter),
    "calendar-spread": ArbitrageStrategyDefinition("calendar-spread", CalendarSpreadStrategyAdapter),
}


def strategy_definition(strategy_id: str) -> ArbitrageStrategyDefinition:
    """Resolve a supported strategy without silently falling back to another one."""
    try:
        return STRATEGIES[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unsupported arbitrage strategy: {strategy_id}") from exc


def strategy_implementation_hash(strategy_id: str) -> str:
    """Hash the registered adapter implementation source, excluding runtime state."""
    definition = strategy_definition(strategy_id)
    try:
        source = inspect.getsource(definition.adapter_type)
    except (OSError, TypeError) as exc:
        raise RuntimeError(f"unable to inspect strategy implementation: {strategy_id}") from exc
    return provenance_hash({"strategy_id": strategy_id, "adapter_source": source})


def build_strategy_adapter(strategy_id: str, parameters: Mapping[str, Any] | None = None) -> Any:
    """Build an independent adapter from persisted run parameters."""
    definition = strategy_definition(strategy_id)
    return definition.adapter_type(**dict(parameters or {}))


__all__ = ["ArbitrageStrategyDefinition", "STRATEGIES", "build_strategy_adapter", "strategy_definition", "strategy_implementation_hash"]
