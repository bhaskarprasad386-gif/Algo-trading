"""Build executable arbitrage candidates from genuine historical chain snapshots.

This layer connects strict chain-position selection to strategy inputs without
inventing strikes, expiries, liquidity, or contract identity. It intentionally
returns candidate contracts only; execution remains the responsibility of the
existing arbitrage adapters/execution simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .arbitrage_chain_selector import (
    ChainContract,
    SelectedPair,
    pair_by_strike,
    select_box_index,
    select_box_stock,
    select_calendar_expiries,
    select_synthetic_index,
    select_synthetic_stock,
)


@dataclass(frozen=True)
class HistoricalChainSnapshot:
    """One point-in-time chain; every contract must belong to this snapshot."""

    timestamp_ns: int
    underlying: str
    expiry: int
    contracts: tuple[ChainContract, ...]
    atm: float

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns cannot be negative")
        if not self.underlying.strip():
            raise ValueError("underlying is required")
        if not self.contracts:
            raise ValueError("historical chain cannot be empty")
        if any(c.timestamp_ns != self.timestamp_ns for c in self.contracts):
            raise ValueError("all contracts must share the snapshot timestamp")
        if any(c.underlying != self.underlying for c in self.contracts):
            raise ValueError("all contracts must share the snapshot underlying")
        if any(c.expiry != self.expiry for c in self.contracts):
            raise ValueError("all contracts must share the snapshot expiry")


class HistoricalArbitrageChainCandidates:
    """Strict candidate builder used before the executable strategy adapters."""

    @staticmethod
    def _liquid(contracts: Sequence[ChainContract], *, min_volume: float,
                min_oi: float, max_spread_pct: float) -> tuple[ChainContract, ...]:
        if min_volume < 0 or min_oi < 0 or max_spread_pct < 0:
            raise ValueError("invalid liquidity policy")
        result: list[ChainContract] = []
        for c in contracts:
            if c.volume < min_volume or c.oi < min_oi:
                continue
            if c.bid <= 0 or c.ask < c.bid:
                continue
            if ((c.ask - c.bid) / c.bid) * 100.0 > max_spread_pct:
                continue
            result.append(c)
        return tuple(result)

    @staticmethod
    def _require_liquid_selected(selected: Sequence[ChainContract], *, min_volume: float,
                                 min_oi: float, max_spread_pct: float) -> tuple[ChainContract, ...]:
        """Validate the exact structural selection; never replace an illiquid leg."""
        if not selected:
            raise ValueError("historical chain is incomplete: no executable positions selected")
        liquid = HistoricalArbitrageChainCandidates._liquid(
            selected, min_volume=min_volume, min_oi=min_oi, max_spread_pct=max_spread_pct
        )
        if len(liquid) != len(selected):
            raise ValueError("historical chain is incomplete: selected positions are not executable")
        return tuple(selected)

    @classmethod
    def box_stock(cls, snapshot: HistoricalChainSnapshot, *, min_volume: float = 0,
                  min_oi: float = 0, max_spread_pct: float = 100.0) -> tuple[ChainContract, ...]:
        selected = select_box_stock(snapshot.contracts, atm=snapshot.atm)
        return cls._require_liquid_selected(selected, min_volume=min_volume, min_oi=min_oi,
                                            max_spread_pct=max_spread_pct)

    @classmethod
    def box_index(cls, snapshot: HistoricalChainSnapshot, *, min_volume: float = 0,
                  min_oi: float = 0, max_spread_pct: float = 100.0) -> tuple[ChainContract, ...]:
        selected = select_box_index(snapshot.contracts, atm=snapshot.atm)
        return cls._require_liquid_selected(selected, min_volume=min_volume, min_oi=min_oi,
                                            max_spread_pct=max_spread_pct)

    @classmethod
    def synthetic_stock(cls, snapshot: HistoricalChainSnapshot, *, min_volume: float = 0,
                        min_oi: float = 0, max_spread_pct: float = 100.0) -> tuple[ChainContract, ...]:
        liquid = cls._liquid(snapshot.contracts, min_volume=min_volume, min_oi=min_oi,
                             max_spread_pct=max_spread_pct)
        return select_synthetic_stock(liquid, atm=snapshot.atm)

    @classmethod
    def synthetic_index(cls, snapshot: HistoricalChainSnapshot, *, min_volume: float = 0,
                        min_oi: float = 0, max_spread_pct: float = 100.0) -> tuple[ChainContract, ...]:
        liquid = cls._liquid(snapshot.contracts, min_volume=min_volume, min_oi=min_oi,
                             max_spread_pct=max_spread_pct)
        return select_synthetic_index(liquid, atm=snapshot.atm)

    @staticmethod
    def calendar_expiry_pairs(contracts: Sequence[ChainContract]) -> tuple[tuple[int, int], ...]:
        return select_calendar_expiries(contracts)

    @staticmethod
    def strike_pairs(contracts: Sequence[ChainContract], *, expiry: int,
                     option_type: str, selected_strikes: set[float] | None = None) -> tuple[SelectedPair, ...]:
        pairs = pair_by_strike(contracts, expiry=expiry, option_type=option_type)
        if selected_strikes is None:
            return pairs
        return tuple(p for p in pairs if p.low.strike in selected_strikes and p.high.strike in selected_strikes)


__all__ = ["HistoricalChainSnapshot", "HistoricalArbitrageChainCandidates"]
