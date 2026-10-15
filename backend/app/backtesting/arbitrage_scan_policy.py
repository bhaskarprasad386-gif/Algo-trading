"""Advanced strike scan policies for executable F&O arbitrage.

A strike distance is an option-chain position count from ATM, never a rupee
price gap. The caller supplies actual contract-master strikes/expiries.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Literal

InstrumentClass = Literal["STOCK", "INDEX", "COMMODITY"]

@dataclass(frozen=True)
class ScanPolicy:
    stock_box_distances: tuple[int, ...] = (3, 4, 5)
    index_box_distances: tuple[int, ...] = tuple(range(3, 16))
    stock_synthetic_radius: int = 7
    index_synthetic_radius: int = 10
    commodity_synthetic_radius: int = 10

    def box_distances(self, instrument_class: InstrumentClass) -> tuple[int, ...]:
        return self.stock_box_distances if instrument_class == "STOCK" else self.index_box_distances

    def synthetic_radius(self, instrument_class: InstrumentClass) -> int:
        if instrument_class == "STOCK":
            return self.stock_synthetic_radius
        if instrument_class == "COMMODITY":
            return self.commodity_synthetic_radius
        return self.index_synthetic_radius

def ordered_strikes_around_atm(strikes: Iterable[float], *, atm_strike: float) -> tuple[float, ...]:
    ordered = tuple(sorted(set(float(s) for s in strikes)))
    if not ordered:
        raise ValueError("option chain must contain at least one strike")
    if float(atm_strike) not in ordered:
        raise ValueError("atm_strike must exist in the supplied option chain")
    return ordered

def strike_distance_from_atm(strikes: Iterable[float], *, atm_strike: float, strike: float) -> int:
    ordered = ordered_strikes_around_atm(strikes, atm_strike=atm_strike)
    try:
        return abs(ordered.index(float(strike)) - ordered.index(float(atm_strike)))
    except ValueError as exc:
        raise ValueError("strike must exist in the supplied option chain") from exc

def enumerate_box_pairs(strikes: Iterable[float], *, atm_strike: float, instrument_class: InstrumentClass,
                        policy: ScanPolicy | None = None) -> tuple[tuple[float, float, int], ...]:
    """Enumerate all valid K1/K2 pairs within the configured chain distance.

    Distance is the position-count separation in the actual chain. This does not
    force either leg to be ATM, allowing liquid non-contiguous K1/K2 boxes.
    """
    policy = policy or ScanPolicy()
    ordered = ordered_strikes_around_atm(strikes, atm_strike=atm_strike)
    allowed = set(policy.box_distances(instrument_class))
    pairs = []
    for low_index, low in enumerate(ordered):
        for high_index in range(low_index + 1, len(ordered)):
            distance = high_index - low_index
            if distance in allowed:
                pairs.append((low, ordered[high_index], distance))
    return tuple(pairs)

def enumerate_synthetic_strikes(strikes: Iterable[float], *, atm_strike: float,
                                instrument_class: InstrumentClass,
                                policy: ScanPolicy | None = None) -> tuple[tuple[float, int, Literal["LOWER","ATM","UPPER"]], ...]:
    policy = policy or ScanPolicy()
    ordered = tuple(sorted(set(float(s) for s in strikes)))
    if not ordered:
        raise ValueError("option chain must contain at least one strike")

    # Index callers pass the live, executable CE+PE pairs after quote/liquidity
    # validation. Choose up to ten nearest liquid strikes on EACH side of ATM.
    # Do not require ATM itself to be liquid/present, and do not apply a fixed
    # position radius: the subscription layer supplies the full actual chain.
    if instrument_class == "INDEX":
        atm = float(atm_strike)
        lower = [strike for strike in ordered if strike < atm]
        upper = [strike for strike in ordered if strike > atm]
        # Lower distances count outward from ATM (1 is nearest).
        lower_selected = [(strike, len(lower[-10:]) - index, "LOWER")
                          for index, strike in enumerate(lower[-10:])]
        result = lower_selected + [(strike, 0, "ATM") for strike in ordered if strike == atm]
        result += [(strike, index + 1, "UPPER") for index, strike in enumerate(upper[:10])]
        return tuple(result)

    # Stock scope deliberately retains its existing fixed actual-chain radius.
    ordered = ordered_strikes_around_atm(ordered, atm_strike=atm_strike)
    atm_index = ordered.index(float(atm_strike))
    radius = policy.synthetic_radius(instrument_class)
    result = []
    for index, strike in enumerate(ordered):
        distance = index - atm_index
        if abs(distance) > radius: continue
        side = "LOWER" if distance < 0 else "UPPER" if distance > 0 else "ATM"
        result.append((strike, abs(distance), side))
    return tuple(result)

__all__ = ["ScanPolicy", "enumerate_box_pairs", "enumerate_synthetic_strikes",
           "ordered_strikes_around_atm", "strike_distance_from_atm"]