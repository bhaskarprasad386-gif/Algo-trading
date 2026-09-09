"""Historical arbitrage chain/universe selection with strict ATM-relative rules.

Selection is deliberately based on ordered strikes present in the supplied
historical chain. It never manufactures missing strikes and never converts a
rupee distance into a chain position.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


# NSE/BSE are equity/index venues; MCX is the supported commodity exchange.
# COMMODITY is retained as a normalized venue alias for existing datasets.
SUPPORTED_VENUES = frozenset({"NSE", "BSE", "MCX", "COMMODITY"})
SUPPORTED_INSTRUMENT_CLASSES = frozenset({"STOCK", "INDEX", "COMMODITY"})


@dataclass(frozen=True)
class ChainContract:
    """One real historical option-chain contract at one observation time."""

    timestamp_ns: int
    venue: str
    underlying: str
    instrument_class: str
    expiry: int
    strike: float
    option_type: str
    lot_size: int = 1
    volume: float = 0.0
    oi: float = 0.0
    bid: float = 0.0
    ask: float = 0.0

    def __post_init__(self) -> None:
        if self.venue not in SUPPORTED_VENUES:
            raise ValueError(f"unsupported venue: {self.venue}")
        if self.instrument_class not in SUPPORTED_INSTRUMENT_CLASSES:
            raise ValueError(f"unsupported instrument class: {self.instrument_class}")
        if self.option_type not in {"CE", "PE"}:
            raise ValueError("option_type must be CE or PE")
        if self.lot_size <= 0 or self.strike < 0 or self.volume < 0 or self.oi < 0:
            raise ValueError("invalid historical contract fields")
        if self.bid < 0 or self.ask < self.bid:
            raise ValueError("invalid historical bid/ask")


@dataclass(frozen=True)
class SelectedPair:
    low: ChainContract
    high: ChainContract
    low_position: int
    high_position: int


def _ordered_strikes(contracts: Iterable[ChainContract], *, timestamp_ns: int,
                     underlying: str, expiry: int, option_type: str) -> list[float]:
    strikes = {
        c.strike for c in contracts
        if c.timestamp_ns == timestamp_ns and c.underlying == underlying
        and c.expiry == expiry and c.option_type == option_type
    }
    return sorted(strikes)


def _atm_index(strikes: Sequence[float], atm: float | None) -> int:
    if not strikes:
        raise ValueError("historical chain is empty")
    if atm is None:
        raise ValueError("historical ATM is required; do not infer it from a fabricated price")
    return min(range(len(strikes)), key=lambda i: (abs(strikes[i] - atm), strikes[i]))


def _select_side_positions(contracts: Sequence[ChainContract], *, atm: float,
                           positions_below: int, positions_above: int,
                           exclude_between: int = 0) -> tuple[ChainContract, ...]:
    if positions_below < 0 or positions_above < 0 or exclude_between < 0:
        raise ValueError("position counts must be non-negative")
    if not contracts:
        return ()
    strikes = sorted({c.strike for c in contracts})
    ai = _atm_index(strikes, atm)
    lower = range(max(0, ai - positions_below), max(0, ai - exclude_between))
    upper = range(min(len(strikes), ai + exclude_between + 1),
                  min(len(strikes), ai + positions_above + 1))
    wanted = set(lower) | set(upper)
    return tuple(sorted((c for c in contracts if strikes.index(c.strike) in wanted),
                        key=lambda c: (c.strike, c.option_type)))


def select_box_stock(contracts: Sequence[ChainContract], *, atm: float) -> tuple[ChainContract, ...]:
    """NIFTY-50 stock Box universe: exactly five positions below and five above ATM."""
    if not contracts or contracts[0].instrument_class != "STOCK":
        raise ValueError("Box stock selection requires stock option contracts")
    selected = _select_side_positions(contracts, atm=atm, positions_below=5, positions_above=5)
    if len(selected) != 10:
        raise ValueError("historical Box stock chain is incomplete: five positions are required on each side")
    return selected


def select_box_index(contracts: Sequence[ChainContract], *, atm: float) -> tuple[ChainContract, ...]:
    """Index Box universe: positions 3 through 15 on each side of ATM."""
    if not contracts or contracts[0].instrument_class != "INDEX":
        raise ValueError("Box index selection requires index option contracts")
    selected = _select_side_positions(
        contracts, atm=atm, positions_below=15, positions_above=15, exclude_between=2
    )
    if len(selected) != 26:
        raise ValueError("historical Box index chain is incomplete: positions 3 through 15 are required on both sides")
    return selected


def select_synthetic_stock(contracts: Sequence[ChainContract], *, atm: float) -> tuple[ChainContract, ...]:
    """Liquid stock Synthetic universe: up to five actual chain positions either side of ATM."""
    if not contracts or contracts[0].instrument_class != "STOCK":
        raise ValueError("Synthetic stock selection requires stock option contracts")
    return _select_side_positions(contracts, atm=atm, positions_below=5, positions_above=5)


def select_synthetic_index(contracts: Sequence[ChainContract], *, atm: float) -> tuple[ChainContract, ...]:
    """Liquid index Synthetic universe: up to fifteen actual chain positions either side of ATM."""
    if not contracts or contracts[0].instrument_class != "INDEX":
        raise ValueError("Synthetic index selection requires index option contracts")
    return _select_side_positions(contracts, atm=atm, positions_below=15, positions_above=15)


def select_calendar_expiries(contracts: Sequence[ChainContract]) -> tuple[tuple[int, int], ...]:
    """Return every real near/far expiry pair present in the historical chain."""
    expiries = sorted({c.expiry for c in contracts})
    return tuple((near, far) for i, near in enumerate(expiries) for far in expiries[i + 1:])


def pair_by_strike(contracts: Sequence[ChainContract], *, expiry: int,
                   option_type: str) -> tuple[SelectedPair, ...]:
    """Build only genuine low/high strike pairs from one historical chain."""
    filtered = [c for c in contracts if c.expiry == expiry and c.option_type == option_type]
    by_strike = {c.strike: c for c in filtered}
    strikes = sorted(by_strike)
    return tuple(SelectedPair(by_strike[lo], by_strike[hi], i, j)
                 for i, lo in enumerate(strikes) for j, hi in enumerate(strikes)
                 if i < j)


__all__ = [
    "SUPPORTED_VENUES", "SUPPORTED_INSTRUMENT_CLASSES", "ChainContract",
    "SelectedPair", "select_box_stock", "select_box_index",
    "select_synthetic_stock", "select_synthetic_index",
    "select_calendar_expiries", "pair_by_strike",
]
