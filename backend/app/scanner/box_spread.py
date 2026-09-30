"""Executable live Box Spread scanner built only from real synchronized option quotes."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
from app.backtesting.arbitrage_backtester import BoxSpreadBacktester, OptionQuote
from app.backtesting.arbitrage_scan_policy import ScanPolicy, enumerate_box_pairs

BOX_SCAN_POLICY = ScanPolicy(
    stock_box_distances=tuple(range(1, 6)),
    index_box_distances=tuple(range(1, 11)),
)

@dataclass(frozen=True)
class BoxSpreadScanConfig:
    fees_per_unit: float = 0.0
    min_executable_edge: float = 0.0
    allowed_stock_symbols: frozenset[str] = frozenset()

@dataclass(frozen=True)
class BoxSpreadScanResult:
    low: OptionQuote
    high: OptionQuote
    direction: str
    executable_edge: float
    edge_per_lot: float
    gross_pnl: float
    strike_distance: int

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
    first = quotes[0]
    if any(q.timestamp_ns != first.timestamp_ns for q in quotes):
        raise ValueError("box option quotes must share timestamp")
    if any(q.underlying != first.underlying or q.expiry != first.expiry or q.instrument_class != cls for q in quotes):
        raise ValueError("box option quotes must share underlying, expiry and instrument class")
    by_strike = {q.strike: q for q in quotes}
    if len(by_strike) != len(quotes):
        raise ValueError("duplicate box strike")
    results = []
    for low_strike, high_strike, distance in enumerate_box_pairs(
        by_strike, atm_strike=float(atm_strike), instrument_class=cls, policy=policy
    ):
        low, high = by_strike.get(low_strike), by_strike.get(high_strike)
        if low is None or high is None:
            continue
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
