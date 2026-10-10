"""Executable live Box Spread scanner built only from real synchronized option quotes."""
from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable
from app.backtesting.arbitrage_backtester import BoxSpreadBacktester, OptionQuote
from app.backtesting.arbitrage_scan_policy import ScanPolicy, ordered_strikes_around_atm, enumerate_box_pairs

BOX_SCAN_POLICY = ScanPolicy(
    stock_box_distances=(1, 2, 3, 4, 5),
    index_box_distances=tuple(range(1, 11)),
)

@dataclass(frozen=True)
class BoxSpreadScanConfig:
    fees_per_unit: float = 0.0
    min_executable_edge: float = 0.0
    allowed_stock_symbols: frozenset[str] = frozenset()
    min_option_volume: int = 1
    min_option_oi: int = 1
    max_option_spread_pct: float = 5.0

@dataclass(frozen=True)
class BoxSpreadScanResult:
    low: OptionQuote
    high: OptionQuote
    direction: str
    executable_edge: float
    edge_per_lot: float
    gross_pnl: float
    strike_distance: int

def _liquid_quote(q: OptionQuote, config: BoxSpreadScanConfig) -> bool:
    if q.volume < config.min_option_volume or q.oi < config.min_option_oi:
        return False
    for bid, ask in ((q.call_bid, q.call_ask), (q.put_bid, q.put_ask)):
        if bid <= 0 or ask <= 0 or ask < bid:
            return False
        if ((ask - bid) / bid) * 100.0 > config.max_option_spread_pct:
            return False
    return True

def _liquid_box_pairs(quotes: dict[float, OptionQuote], *, atm_strike: float,
                      instrument_class: str, policy: ScanPolicy) -> tuple[tuple[float, float, int], ...]:
    ordered = ordered_strikes_around_atm(quotes.keys(), atm_strike=float(atm_strike))
    allowed_max = max(policy.box_distances(instrument_class))
    liquid = [s for s in ordered if _liquid_quote(quotes[s], BoxSpreadScanConfig(
        min_option_volume=1, min_option_oi=1, max_option_spread_pct=100.0
    ))]
    # Pair only liquid strikes; they need not be consecutive or ATM-adjacent.
    pairs = []
    for low, high in combinations(liquid, 2):
        distance = abs(ordered.index(high) - ordered.index(low))
        if 1 <= distance <= allowed_max:
            pairs.append((low, high, distance))
    return tuple(pairs)

def scan_box_snapshot(option_quotes: Iterable[OptionQuote], *, atm_strike: float,
                       instrument_class: str, config: BoxSpreadScanConfig | None = None,
                       policy: ScanPolicy | None = None) -> tuple[BoxSpreadScanResult, ...]:
    config = config or BoxSpreadScanConfig()
    policy = policy or BOX_SCAN_POLICY
    cls = str(instrument_class).strip().upper()
    quotes = tuple(option_quotes)
    if not quotes:
        return ()
    if cls not in {"STOCK", "INDEX"}:
        raise ValueError("box scanner supports only STOCK and INDEX")
    if cls == "STOCK" and quotes[0].underlying.upper() not in {s.upper() for s in config.allowed_stock_symbols}:
        raise ValueError("stock is outside the configured Box Spread stock universe")
    if config.min_option_volume < 0 or config.min_option_oi < 0 or config.max_option_spread_pct < 0:
        raise ValueError("invalid box liquidity policy")
    first = quotes[0]
    if any(q.timestamp_ns != first.timestamp_ns for q in quotes):
        raise ValueError("box option quotes must share timestamp")
    if any(q.underlying != first.underlying or q.expiry != first.expiry or q.instrument_class != cls for q in quotes):
        raise ValueError("box option quotes must share underlying, expiry and instrument class")
    by_strike = {q.strike: q for q in quotes}
    if len(by_strike) != len(quotes):
        raise ValueError("duplicate box strike")

    ordered = ordered_strikes_around_atm(by_strike, atm_strike=float(atm_strike))
    pairs = enumerate_box_pairs(
        by_strike.keys(), atm_strike=float(atm_strike), instrument_class=cls, policy=policy
    )
    liquid = {
        strike for strike, quote in by_strike.items()
        if _liquid_quote(quote, config)
    }
    results = []
    for low_strike, high_strike, distance in pairs:
        if low_strike not in liquid or high_strike not in liquid:
            continue
        low, high = by_strike[low_strike], by_strike[high_strike]
        for direction in ("LONG", "SHORT"):
            opp = BoxSpreadBacktester.evaluate(low, high, direction=direction, fees_per_unit=config.fees_per_unit)
            if opp is None or opp.executable_edge < config.min_executable_edge:
                continue
            results.append(BoxSpreadScanResult(
                low, high, direction, opp.executable_edge, opp.edge_per_lot, opp.gross_pnl, distance
            ))
    results.sort(key=lambda x: (x.executable_edge, x.edge_per_lot, x.gross_pnl), reverse=True)
    return tuple(results)

__all__ = ["BOX_SCAN_POLICY", "BoxSpreadScanConfig", "BoxSpreadScanResult", "scan_box_snapshot"]
