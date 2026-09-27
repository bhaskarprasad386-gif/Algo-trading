"""Fast, pure synthetic Cash-Carry scanner core.

The scanner consumes already-normalized live snapshots. It does not fetch from a
broker, invent strikes, or mutate storage. A caller supplies the point-in-time
option chain, future quote, ATM and (for stocks) the permitted NIFTY-50 universe.
Index scope remains the locked ATM +/- 15 actual chain positions; stock scope is
ATM +/- 5 actual chain positions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.backtesting.arbitrage_backtester import (
    FutureQuote,
    LiquidityPolicy,
    OptionQuote,
    SyntheticCashCarryBacktester,
)
from app.backtesting.arbitrage_scan_policy import (
    ScanPolicy,
    enumerate_synthetic_strikes,
)


@dataclass(frozen=True)
class SyntheticScanConfig:
    """Runtime filters; strike radii remain locked by ScanPolicy."""

    rate: float = 0.0
    time_to_expiry_years: float = 0.0
    fees_per_unit: float = 0.0
    liquidity: LiquidityPolicy = LiquidityPolicy()
    min_executable_edge: float = 0.0
    allowed_stock_symbols: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.time_to_expiry_years < 0:
            raise ValueError("time_to_expiry_years must be non-negative")
        if self.fees_per_unit < 0:
            raise ValueError("fees_per_unit must be non-negative")
        if self.min_executable_edge < 0:
            raise ValueError("min_executable_edge must be non-negative")
        if any(not isinstance(s, str) or not s.strip() for s in self.allowed_stock_symbols):
            raise ValueError("allowed_stock_symbols must contain non-empty strings")


@dataclass(frozen=True)
class SyntheticScanResult:
    option: OptionQuote
    future: FutureQuote
    direction: str
    executable_edge: float
    edge_per_lot: float
    gross_pnl: float
    strike_distance: int
    strike_side: str


def _validate_universe(
    *,
    future: FutureQuote,
    allowed_stock_symbols: frozenset[str],
) -> None:
    symbol = future.underlying.strip().upper()
    if future.instrument_class == "STOCK":
        if not allowed_stock_symbols:
            raise ValueError("NIFTY-50 stock universe is required for stock synthetic scanning")
        if symbol not in {s.strip().upper() for s in allowed_stock_symbols}:
            raise ValueError("stock is outside the configured NIFTY-50 universe")
    elif future.instrument_class != "INDEX":
        raise ValueError("synthetic scanner supports only STOCK and INDEX")


def scan_synthetic_snapshot(
    option_quotes: Iterable[OptionQuote],
    future: FutureQuote,
    *,
    atm_strike: float,
    config: SyntheticScanConfig | None = None,
    policy: ScanPolicy | None = None,
) -> tuple[SyntheticScanResult, ...]:
    """Scan one timestamp and return executable opportunities highest-first.

    Only actual strikes present in the supplied snapshot are considered.
    Both LONG and SHORT synthetic-vs-future directions are evaluated.
    """
    config = config or SyntheticScanConfig()
    policy = policy or ScanPolicy()
    _validate_universe(
        future=future,
        allowed_stock_symbols=config.allowed_stock_symbols,
    )

    quotes = tuple(option_quotes)
    if not quotes:
        return ()
    if any(q.timestamp_ns != future.timestamp_ns for q in quotes):
        raise ValueError("option and future quotes must share timestamp")
    if any(q.underlying != future.underlying for q in quotes):
        raise ValueError("option and future quotes must share underlying")
    if any(q.expiry != future.expiry for q in quotes):
        raise ValueError("option and future quotes must share expiry")
    if any(q.instrument_class != future.instrument_class for q in quotes):
        raise ValueError("option and future quotes must share instrument class")

    by_strike: dict[float, OptionQuote] = {}
    for quote in quotes:
        if quote.strike in by_strike:
            raise ValueError(f"duplicate option strike: {quote.strike}")
        by_strike[quote.strike] = quote

    selected = enumerate_synthetic_strikes(
        by_strike, atm_strike=atm_strike,
        instrument_class=future.instrument_class, policy=policy,
    )
    results: list[SyntheticScanResult] = []
    for strike, distance, side in selected:
        if distance == 0:
            continue
        option = by_strike[strike]
        for direction in ("LONG", "SHORT"):
            opportunity = SyntheticCashCarryBacktester.evaluate(
                option,
                future,
                rate=config.rate,
                time_to_expiry_years=config.time_to_expiry_years,
                fees_per_unit=config.fees_per_unit,
                direction=direction,
                liquidity=config.liquidity,
            )
            if opportunity is None or opportunity.executable_edge < config.min_executable_edge:
                continue
            results.append(
                SyntheticScanResult(
                    option=option,
                    future=future,
                    direction=direction,
                    executable_edge=opportunity.executable_edge,
                    edge_per_lot=opportunity.edge_per_lot,
                    gross_pnl=opportunity.gross_pnl,
                    strike_distance=distance,
                    strike_side=side,
                )
            )

    results.sort(
        key=lambda x: (
            x.executable_edge,
            x.edge_per_lot,
            x.gross_pnl,
            -x.strike_distance,
        ),
        reverse=True,
    )
    return tuple(results)


__all__ = ["SyntheticScanConfig", "SyntheticScanResult", "scan_synthetic_snapshot"]
