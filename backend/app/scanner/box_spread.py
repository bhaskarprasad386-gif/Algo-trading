"""Executable live Box Spread scanner built only from real synchronized option quotes."""
from __future__ import annotations
from dataclasses import dataclass, replace
from itertools import combinations
from math import isfinite
from typing import Iterable
from app.backtesting.arbitrage_backtester import BoxSpreadBacktester, OptionQuote
from app.backtesting.arbitrage_scan_policy import ScanPolicy, ordered_strikes_around_atm, enumerate_box_pairs

BOX_SCAN_POLICY = ScanPolicy(
    stock_box_distances=(3, 4, 5),
    index_box_distances=tuple(range(3, 16)),
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

def _whole_nonnegative(value) -> bool:
    try:
        number = float(value)
        return isfinite(number) and number >= 0 and number.is_integer()
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_quote(q: OptionQuote) -> bool:
    try:
        strike = float(q.strike)
        lot_size = float(q.lot_size)
        timestamp = int(q.timestamp_ns)
        leg_timestamps = tuple(
            int(value if value is not None else q.timestamp_ns)
            for value in (q.call_timestamp_ns, q.put_timestamp_ns)
        )
        prices = (q.call_bid, q.call_ask, q.put_bid, q.put_ask)
        if not isfinite(strike) or strike <= 0:
            return False
        if not isfinite(lot_size) or lot_size <= 0 or not lot_size.is_integer():
            return False
        if timestamp <= 0 or any(value <= 0 for value in leg_timestamps):
            return False
        if any(not isfinite(float(value)) or float(value) <= 0 for value in prices):
            return False
        if not _whole_nonnegative(q.volume) or not _whole_nonnegative(q.oi):
            return False
        return float(q.call_ask) >= float(q.call_bid) and float(q.put_ask) >= float(q.put_bid)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return False


def _liquid_quote(q: OptionQuote, config: BoxSpreadScanConfig) -> bool:
    if not _valid_quote(q):
        return False
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
    pairs = []
    for low, high in combinations(liquid, 2):
        distance = abs(ordered.index(high) - ordered.index(low))
        if 1 <= distance <= allowed_max:
            pairs.append((low, high, distance))
    return tuple(pairs)

def scan_box_snapshot(option_quotes: Iterable[OptionQuote], *, atm_strike: float,
                      instrument_class: str, config: BoxSpreadScanConfig | None = None,
                      policy: ScanPolicy | None = None,
                      diagnostics: dict[str, int] | None = None,
                      include_nonpositive: bool = False,
                      include_all_strike_pairs: bool = False) -> tuple[BoxSpreadScanResult, ...]:
    """Scan a snapshot; optionally fill per-snapshot rejection counters for live diagnostics."""
    stats = diagnostics if diagnostics is not None else {}
    for key in ("quotes", "pairs_considered", "liquidity_rejected_pairs", "timestamp_skew_rejected_pairs",
                "backtest_rejected_directions", "edge_rejected_directions", "qualified_directions"):
        stats[key] = 0
    config = config or BoxSpreadScanConfig()
    policy = policy or BOX_SCAN_POLICY
    cls = str(instrument_class).strip().upper()
    quotes = tuple(option_quotes)
    stats["quotes"] = len(quotes)
    if not quotes:
        return ()
    if cls not in {"STOCK", "INDEX"}:
        raise ValueError("box scanner supports only STOCK and INDEX")
    if cls == "STOCK" and quotes[0].underlying.upper() not in {s.upper() for s in config.allowed_stock_symbols}:
        raise ValueError("stock is outside the configured Box Spread stock universe")
    try:
        atm_value = float(atm_strike)
        fees = float(config.fees_per_unit)
        min_edge = float(config.min_executable_edge)
        max_spread = float(config.max_option_spread_pct)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid Box Spread scan configuration")
    if (not isfinite(atm_value) or atm_value <= 0 or not isfinite(fees)
            or not isfinite(min_edge) or not isfinite(max_spread)
            or config.min_option_volume < 0 or config.min_option_oi < 0 or max_spread < 0):
        raise ValueError("invalid Box Spread scan configuration")
    first = quotes[0]
    if any(q.underlying != first.underlying or q.expiry != first.expiry or q.instrument_class != cls for q in quotes):
        raise ValueError("box option quotes must share underlying, expiry and instrument class")
    quotes = tuple(q for q in quotes if _valid_quote(q))
    stats["invalid_quotes"] = stats.get("quotes", 0) - len(quotes)
    if not quotes:
        return ()
    by_strike = {q.strike: q for q in quotes}
    if len(by_strike) != len(quotes):
        raise ValueError("duplicate box strike")

    if include_all_strike_pairs:
        ordered = ordered_strikes_around_atm(by_strike.keys(), atm_strike=float(atm_strike))
        pairs = tuple(
            (low_strike, high_strike, high_index - low_index)
            for low_index, low_strike in enumerate(ordered)
            for high_index, high_strike in enumerate(ordered[low_index + 1:], start=low_index + 1)
        )
    else:
        pairs = enumerate_box_pairs(
            by_strike.keys(), atm_strike=float(atm_strike), instrument_class=cls, policy=policy
        )
    liquid = {strike for strike, quote in by_strike.items() if _liquid_quote(quote, config)}
    results = []
    for low_strike, high_strike, distance in pairs:
        stats["pairs_considered"] += 1
        if low_strike not in liquid or high_strike not in liquid:
            stats["liquidity_rejected_pairs"] += 1
            continue
        low, high = by_strike[low_strike], by_strike[high_strike]
        leg_timestamps = (
            low.call_timestamp_ns if low.call_timestamp_ns is not None else low.timestamp_ns,
            low.put_timestamp_ns if low.put_timestamp_ns is not None else low.timestamp_ns,
            high.call_timestamp_ns if high.call_timestamp_ns is not None else high.timestamp_ns,
            high.put_timestamp_ns if high.put_timestamp_ns is not None else high.timestamp_ns,
        )
        if max(leg_timestamps) - min(leg_timestamps) > 3_000_000_000:
            stats["timestamp_skew_rejected_pairs"] += 1
            continue
        anchor_ts = max(leg_timestamps)
        eval_low = replace(low, timestamp_ns=anchor_ts)
        eval_high = replace(high, timestamp_ns=anchor_ts)
        for direction in ("LONG", "SHORT"):
            opp = BoxSpreadBacktester.evaluate(eval_low, eval_high, direction=direction, fees_per_unit=config.fees_per_unit)
            if opp is None:
                # Position exits need the actual edge at zero/negative values too:
                # BoxSpreadBacktester intentionally omits non-positive opportunities.
                if not include_nonpositive:
                    stats["backtest_rejected_directions"] += 1
                    continue
                width = high_strike - low_strike
                if direction == "LONG":
                    debit = (low.call_ask - high.call_bid) + (high.put_ask - low.put_bid)
                    edge = width - debit - config.fees_per_unit
                else:
                    credit = (low.call_bid - high.call_ask) + (high.put_bid - low.put_ask)
                    edge = credit - width - config.fees_per_unit
                if edge < config.min_executable_edge:
                    stats["edge_rejected_directions"] += 1
                    continue
                edge_per_lot = edge * low.lot_size
                results.append(BoxSpreadScanResult(
                    low, high, direction, edge, edge_per_lot, edge_per_lot, distance
                ))
                continue
            if opp.executable_edge < config.min_executable_edge:
                stats["edge_rejected_directions"] += 1
                continue
            stats["qualified_directions"] += 1
            results.append(BoxSpreadScanResult(
                low, high, direction, opp.executable_edge, opp.edge_per_lot, opp.gross_pnl, distance
            ))
    results.sort(key=lambda x: (x.executable_edge, x.edge_per_lot, x.gross_pnl), reverse=True)
    return tuple(results)

__all__ = ["BOX_SCAN_POLICY", "BoxSpreadScanConfig", "BoxSpreadScanResult", "scan_box_snapshot"]
