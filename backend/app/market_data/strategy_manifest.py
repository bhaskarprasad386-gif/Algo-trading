"""Strategy plug-in manifest and dependency contract.

A manifest describes data requirements without granting a strategy broker access.
It is intentionally small so future strategy workspaces can discover capabilities
without coupling strategies to one another.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

@dataclass(frozen=True)
class StrategyManifest:
    strategy_id: str
    display_name: str
    timeframes: tuple[str, ...] = ("1s",)
    instrument_types: tuple[str, ...] = ()
    exchanges: tuple[str, ...] = ()
    max_symbols: int = 0
    features: tuple[str, ...] = ()
    live_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.display_name.strip():
            raise ValueError("strategy_id and display_name are required")
        if not self.timeframes or any(not str(x).strip() for x in self.timeframes):
            raise ValueError("timeframes must be non-empty")
        if self.max_symbols < 0:
            raise ValueError("max_symbols cannot be negative")

    @property
    def normalized_id(self) -> str:
        return self.strategy_id.strip().lower()

    def requires(self, *, timeframe: str | None = None, instrument_type: str | None = None, exchange: str | None = None) -> bool:
        return (
            (timeframe is None or timeframe in self.timeframes)
            and (instrument_type is None or not self.instrument_types or instrument_type in self.instrument_types)
            and (exchange is None or not self.exchanges or exchange.upper() in {x.upper() for x in self.exchanges})
        )

class StrategyManifestRegistry:
    def __init__(self) -> None:
        self._items: dict[str, StrategyManifest] = {}

    def register(self, manifest: StrategyManifest) -> None:
        key = manifest.normalized_id
        if key in self._items and self._items[key] != manifest:
            raise ValueError(f"strategy manifest already registered: {key}")
        self._items[key] = manifest

    def get(self, strategy_id: str) -> StrategyManifest | None:
        return self._items.get(str(strategy_id).strip().lower())

    def all(self) -> tuple[StrategyManifest, ...]:
        return tuple(self._items[k] for k in sorted(self._items))

__all__ = ["StrategyManifest", "StrategyManifestRegistry"]
