"""Deterministic universe and actual-chain-position selection for arbitrage.

This module deliberately works on a supplied historical/live contract catalog.
It never invents strikes, expiries, liquidity, or exchange membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Exchange = Literal["NSE", "BSE", "MCX"]
AssetClass = Literal["EQUITY_FNO", "INDEX_FNO", "COMMODITY"]


@dataclass(frozen=True)
class ArbitrageContract:
    exchange: Exchange
    asset_class: AssetClass
    symbol: str
    expiry: int
    strike: float | None = None
    option_type: Literal["CALL", "PUT"] | None = None
    instrument_id: str = ""
    lot_size: int = 1
    supported: bool = True
    volume: int = 0
    oi: int = 0
    bid: float = 0.0
    ask: float = 0.0


@dataclass(frozen=True)
class StrikePosition:
    strike: float
    position: int
    side: Literal["BELOW_ATM", "ABOVE_ATM"]


def _option_strikes(contracts: Iterable[ArbitrageContract]) -> list[float]:
    return sorted({c.strike for c in contracts if c.supported and c.strike is not None})


def ordered_strike_positions(contracts: Iterable[ArbitrageContract], *, atm: float) -> tuple[StrikePosition, ...]:
    """Return actual chain positions around the supplied ATM, not rupee gaps."""
    strikes = _option_strikes(contracts)
    below = sorted((s for s in strikes if s < atm), reverse=True)
    above = sorted(s for s in strikes if s > atm)
    result: list[StrikePosition] = []
    for position, strike in enumerate(below, 1):
        result.append(StrikePosition(strike, position, "BELOW_ATM"))
    for position, strike in enumerate(above, 1):
        result.append(StrikePosition(strike, position, "ABOVE_ATM"))
    return tuple(result)


def _positions(contracts: Iterable[ArbitrageContract], *, atm: float, lo: int, hi: int) -> tuple[float, ...]:
    positions = ordered_strike_positions(contracts, atm=atm)
    return tuple(p.strike for p in positions if lo <= p.position <= hi)


class ArbitrageUniversePolicy:
    """Locked universe rules for Calendar, Box and Synthetic strategies."""

    @staticmethod
    def calendar(contracts: Iterable[ArbitrageContract]) -> tuple[ArbitrageContract, ...]:
        """All supported NSE/BSE F&O plus supported commodity derivatives."""
        return tuple(c for c in contracts if c.supported and (
            (c.exchange in {"NSE", "BSE"} and c.asset_class in {"EQUITY_FNO", "INDEX_FNO"})
            or c.asset_class == "COMMODITY"
        ))

    @staticmethod
    def box_stock_strikes(contracts: Iterable[ArbitrageContract], *, atm: float) -> tuple[float, ...]:
        """Exactly five actual chain positions below and five above ATM."""
        selected = _positions(contracts, atm=atm, lo=1, hi=5)
        if len(selected) != 10:
            raise ValueError("box stock chain must contain exactly 5 positions on each ATM side")
        return selected

    @staticmethod
    def box_index_strikes(contracts: Iterable[ArbitrageContract], *, atm: float) -> tuple[float, ...]:
        """Index positions 3 through 15 on each side of ATM."""
        positions = ordered_strike_positions(contracts, atm=atm)
        selected = tuple(p.strike for p in positions if 3 <= p.position <= 15)
        below = sum(p.side == "BELOW_ATM" and 3 <= p.position <= 15 for p in positions)
        above = sum(p.side == "ABOVE_ATM" and 3 <= p.position <= 15 for p in positions)
        if below != 13 or above != 13:
            raise ValueError("box index chain must contain positions 3 through 15 on both ATM sides")
        return selected

    @staticmethod
    def synthetic_stock_strikes(contracts: Iterable[ArbitrageContract], *, atm: float) -> tuple[float, ...]:
        """Stock candidates up to five actual positions on each side."""
        return _positions(contracts, atm=atm, lo=1, hi=5)

    @staticmethod
    def synthetic_index_strikes(contracts: Iterable[ArbitrageContract], *, atm: float) -> tuple[float, ...]:
        """Index candidates up to fifteen actual positions on each side."""
        return _positions(contracts, atm=atm, lo=1, hi=15)

    @staticmethod
    def liquid(contracts: Iterable[ArbitrageContract], *, min_volume: int = 0,
               min_oi: int = 0, max_spread_pct: float = 100.0) -> tuple[ArbitrageContract, ...]:
        """Filter using genuine catalog liquidity fields; no estimates are created."""
        if min_volume < 0 or min_oi < 0 or max_spread_pct < 0:
            raise ValueError("invalid liquidity policy")
        result = []
        for c in contracts:
            if not c.supported or c.volume < min_volume or c.oi < min_oi:
                continue
            if c.bid < 0 or c.ask < c.bid:
                continue
            if c.bid == 0:
                if c.ask != 0:
                    continue
            elif ((c.ask - c.bid) / c.bid) * 100.0 > max_spread_pct:
                continue
            result.append(c)
        return tuple(result)


__all__ = ["ArbitrageContract", "ArbitrageUniversePolicy", "StrikePosition",
           "ordered_strike_positions"]
