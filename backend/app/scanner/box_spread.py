"""Executable live Box Spread scanner built only from real synchronized option quotes."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from itertools import combinations
from math import isfinite
from decimal import Decimal, InvalidOperation
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
    max_executable_lots: int = 0
    # Live-only execution gate; raw L1 values are retained for diagnostics.
    tradability_status: str = "UNVERIFIED"
    tradability_reason: str | None = None
    l1_reference_edge: float | None = None
    depth_edge_per_unit: float | None = None
    depth_edge_per_lot: float | None = None
    depth_executable_lots: int = 0
    depth_quantity: int = 0
    depth_legs: tuple = ()
    depth_entry_prices: dict = field(default_factory=dict)
    depth_entry_type: str | None = None
    depth_net_entry_price: float | None = None

def _whole_nonnegative(value) -> bool:
    try:
        number = float(value)
        return isfinite(number) and number >= 0 and number.is_integer()
    except (TypeError, ValueError, OverflowError):
        return False


def _positive_integer(value) -> int | None:
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number > 0 and number == number.to_integral_value() else None
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def _valid_quote(q: OptionQuote) -> bool:
    try:
        strike = float(q.strike)
        lot_size = float(q.lot_size)
        timestamp = _positive_integer(q.timestamp_ns)
        leg_timestamps = tuple(
            _positive_integer(value if value is not None else q.timestamp_ns)
            for value in (q.call_timestamp_ns, q.put_timestamp_ns)
        )
        prices = (q.call_bid, q.call_ask, q.put_bid, q.put_ask)
        if not isfinite(strike) or strike <= 0:
            return False
        if not isfinite(lot_size) or lot_size <= 0 or not lot_size.is_integer():
            return False
        if timestamp is None or any(value is None for value in leg_timestamps):
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

def _max_executable_lots(low: OptionQuote, high: OptionQuote, direction: str) -> int:
    """Return top-of-book executable lots across all four legs; fail closed on missing depth."""
    if direction == "LONG":
        quantities = (low.call_ask_qty, high.call_bid_qty, high.put_ask_qty, low.put_bid_qty)
    elif direction == "SHORT":
        quantities = (low.call_bid_qty, high.call_ask_qty, high.put_bid_qty, low.put_ask_qty)
    else:
        return 0
    lot_size = _positive_integer(low.lot_size)
    high_lot_size = _positive_integer(high.lot_size)
    if lot_size is None or high_lot_size is None or high_lot_size != lot_size:
        return 0
    parsed = []
    for value in quantities:
        quantity = _positive_integer(value)
        if quantity is None:
            return 0
        parsed.append(quantity)
    return min(parsed) // lot_size


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
    if (not isfinite(atm_value) or atm_value <= 0 or not isfinite(fees) or fees < 0
            or not isfinite(min_edge) or not isfinite(max_spread)
            or not _whole_nonnegative(config.min_option_volume)
            or not _whole_nonnegative(config.min_option_oi) or max_spread < 0):
        raise ValueError("invalid Box Spread scan configuration")
    config = replace(
        config,
        fees_per_unit=fees,
        min_executable_edge=min_edge,
        min_option_volume=int(config.min_option_volume),
        min_option_oi=int(config.min_option_oi),
        max_option_spread_pct=max_spread,
    )
    first = quotes[0]
    if any(q.underlying != first.underlying or q.expiry != first.expiry or q.instrument_class != cls for q in quotes):
        raise ValueError("box option quotes must share underlying, expiry and instrument class")
    # A crossed market is a snapshot-level integrity failure: do not build
    # boxes from the remaining strikes while silently dropping the bad quote.
    if any(
        (float(q.call_ask) < float(q.call_bid)) or (float(q.put_ask) < float(q.put_bid))
        for q in quotes
        if all(value is not None for value in (q.call_bid, q.call_ask, q.put_bid, q.put_ask))
    ):
        stats["invalid_quotes"] = stats.get("quotes", 0)
        stats["crossed_quote_snapshots"] = stats.get("crossed_quote_snapshots", 0) + 1
        return ()
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
        if max(leg_timestamps) - min(leg_timestamps) > 8_000_000_000:
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
                    low, high, direction, edge, edge_per_lot, edge_per_lot, distance,
                    _max_executable_lots(low, high, direction),
                ))
                continue
            if opp.executable_edge < config.min_executable_edge:
                stats["edge_rejected_directions"] += 1
                continue
            stats["qualified_directions"] += 1
            results.append(BoxSpreadScanResult(
                low, high, direction, opp.executable_edge, opp.edge_per_lot, opp.gross_pnl, distance,
                _max_executable_lots(low, high, direction),
            ))
    results.sort(key=lambda x: (x.executable_edge, x.edge_per_lot, x.gross_pnl), reverse=True)
    return tuple(results)

__all__ = ["BOX_SCAN_POLICY", "BoxSpreadScanConfig", "BoxSpreadScanResult", "scan_box_snapshot"]
