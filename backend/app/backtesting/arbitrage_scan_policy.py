"""Advanced strike scan policies for executable F&O arbitrage.

A strike distance is an option-chain position count from ATM, never a rupee
price gap. The caller supplies the actual contract-master strikes/expiries, so
missing strikes or expiries are never fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

InstrumentClass = Literal["STOCK", "INDEX"]


@dataclass(frozen=True)
class ScanPolicy:
    """Production scan limits for the requested stock/index universes."""

    stock_box_distances: tuple[int, ...] = (3, 4, 5)
    index_box_distances: tuple[int, ...] = tuple(range(3, 16))
    stock_synthetic_radius: int = 5
    index_synthetic_radius: int = 15

    def box_distances(self, instrument_class: InstrumentClass) -> tuple[int, ...]:
        return (
            self.stock_box_distances
            if instrument_class == "STOCK"
            else self.index_box_distances
        )

    def synthetic_radius(self, instrument_class: InstrumentClass) -> int:
        return (
            self.stock_synthetic_radius
            if instrument_class == "STOCK"
            else self.index_synthetic_radius
        )


def ordered_strikes_around_atm(
    strikes: Iterable[float], *, atm_strike: float
) -> tuple[float, ...]:
    """Return the actual chain strikes in ascending order; ATM must exist."""
    ordered = tuple(sorted(set(float(s) for s in strikes)))
    if not ordered:
        raise ValueError("option chain must contain at least one strike")
    if float(atm_strike) not in ordered:
        raise ValueError("atm_strike must exist in the supplied option chain")
    return ordered


def strike_distance_from_atm(
    strikes: Iterable[float], *, atm_strike: float, strike: float
) -> int:
    ordered = ordered_strikes_around_atm(strikes, atm_strike=atm_strike)
    try:
        atm_index = ordered.index(float(atm_strike))
        strike_index = ordered.index(float(strike))
    except ValueError as exc:
        raise ValueError("strike must exist in the supplied option chain") from exc
    return abs(strike_index - atm_index)


def enumerate_box_pairs(
    strikes: Iterable[float],
    *,
    atm_strike: float,
    instrument_class: InstrumentClass,
    policy: ScanPolicy | None = None,
) -> tuple[tuple[float, float, int], ...]:
    """Enumerate every actual pair whose chain-position distance is allowed.

    Example: with ``ATM, S1, S2, S3``, distance 3 means the third strike
    position, regardless of whether the rupee interval is 50, 100, 250, etc.
    Pairs on either side of ATM and pairs crossing ATM are included.
    """
    policy = policy or ScanPolicy()
    ordered = ordered_strikes_around_atm(strikes, atm_strike=atm_strike)
    allowed = set(policy.box_distances(instrument_class))
    pairs: list[tuple[float, float, int]] = []
    for i, low in enumerate(ordered):
        for j in range(i + 1, len(ordered)):
            distance = j - i
            if distance in allowed:
                pairs.append((low, ordered[j], distance))
    return tuple(pairs)


def enumerate_synthetic_strikes(
    strikes: Iterable[float],
    *,
    atm_strike: float,
    instrument_class: InstrumentClass,
    policy: ScanPolicy | None = None,
) -> tuple[tuple[float, int, Literal["LOWER", "ATM", "UPPER"]], ...]:
    """Return actual strikes within the configured radius on both ATM sides."""
    policy = policy or ScanPolicy()
    ordered = ordered_strikes_around_atm(strikes, atm_strike=atm_strike)
    atm_index = ordered.index(float(atm_strike))
    radius = policy.synthetic_radius(instrument_class)
    result: list[tuple[float, int, Literal["LOWER", "ATM", "UPPER"]]] = []
    for index, strike in enumerate(ordered):
        distance = index - atm_index
        if abs(distance) > radius:
            continue
        side: Literal["LOWER", "ATM", "UPPER"]
        if distance < 0:
            side = "LOWER"
        elif distance > 0:
            side = "UPPER"
        else:
            side = "ATM"
        result.append((strike, abs(distance), side))
    return tuple(result)


__all__ = [
    "ScanPolicy",
    "enumerate_box_pairs",
    "enumerate_synthetic_strikes",
    "ordered_strikes_around_atm",
    "strike_distance_from_atm",
]
