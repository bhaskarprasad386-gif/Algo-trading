"""Pure early-entry condition evaluators. No I/O, broker calls, or order placement."""
from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

from .models import AlertCandidate, AlertState, OptionObservation, SpotBar, StrategyEvaluation

NS = 1_000_000_000


def _positive(value: float | int | None) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def _structurally_valid(row: OptionObservation) -> tuple[str, ...]:
    errors: list[str] = []
    if not row.token or not row.expiry or row.underlying.upper() != "NIFTY": errors.append("WRONG_UNIVERSE")
    if row.option_type not in {"CE", "PE"}: errors.append("INVALID_OPTION_TYPE")
    if row.source_timestamp_ns <= 0 or row.spot_timestamp_ns <= 0: errors.append("INVALID_SOURCE_TIMESTAMP")
    if not _positive(row.spot_price): errors.append("INVALID_SPOT")
    if not _positive(row.iv): errors.append("INVALID_IV")
    if row.iv_input != "BID_ASK_MIDPOINT": errors.append("IV_NOT_BID_ASK_MIDPOINT")
    if not (_positive(row.bid) and _positive(row.ask) and row.ask >= row.bid): errors.append("INVALID_MARKET_DEPTH")
    if row.oi is None or row.oi < 0 or row.oi_timestamp_ns is None or row.oi_timestamp_ns <= 0: errors.append("INVALID_OI")
    if row.volume is None or row.volume < 0: errors.append("INVALID_VOLUME")
    return tuple(errors)


def _fresh(row: OptionObservation, now_ns: int) -> tuple[str, ...]:
    errors: list[str] = []
    if row.source_timestamp_ns > now_ns or now_ns-row.source_timestamp_ns > 5*NS: errors.append("STALE_OPTION_QUOTE")
    if row.spot_timestamp_ns > now_ns or now_ns-row.spot_timestamp_ns > 5*NS: errors.append("STALE_SPOT")
    if row.oi_timestamp_ns is None or row.oi_timestamp_ns > now_ns or now_ns-row.oi_timestamp_ns > 300*NS: errors.append("STALE_OI")
    return tuple(errors)


def _same_contract(rows: Sequence[OptionObservation], option_type: str) -> bool:
    return bool(rows) and all(
        row.option_type == option_type and row.expiry == rows[0].expiry
        and row.strike == rows[0].strike and row.token == rows[0].token
        for row in rows
    )


def evaluate_strategy_a(*, now_ns: int, expiry: str, bars: Sequence[SpotBar],
                         ce: Sequence[OptionObservation], pe: Sequence[OptionObservation],
                         target: Sequence[OptionObservation], direction: str) -> StrategyEvaluation:
    """Early IV/volume momentum trigger; breakout candle close is not required.

    CE/PE each contain three chronological one-minute IV observations. Target
    contains five chronological one-minute cumulative-volume observations:
    the first three increments form the baseline and the fourth is compared.
    Only the newest quote must meet the live 5-second freshness gate.
    """
    direction = direction.upper()
    if direction not in {"BULLISH", "BEARISH"}:
        return StrategyEvaluation(AlertState.INVALIDATED, reasons=("INVALID_DIRECTION",))
    if len(bars) < 15:
        return StrategyEvaluation(AlertState.WATCH, reasons=("INSUFFICIENT_SPOT_BUCKETS",))
    last_bars = tuple(bars[-15:])
    stamps = [b.timestamp_ns for b in last_bars]
    if any(b.timestamp_ns <= 0 for b in last_bars) or any(b <= a for a, b in zip(stamps, stamps[1:])):
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_SPOT_BUCKET_ORDER",))
    if any(not 30*NS <= b-a <= 90*NS for a, b in zip(stamps, stamps[1:])):
        return StrategyEvaluation(AlertState.WATCH, reasons=("MISSING_SPOT_BUCKET",))
    if max(b.high for b in last_bars)-min(b.low for b in last_bars) > 55:
        return StrategyEvaluation(AlertState.WATCH, reasons=("NOT_CONSOLIDATING",))
    if len(ce) < 3 or len(pe) < 3 or len(target) < 5:
        return StrategyEvaluation(AlertState.WATCH, reasons=("INSUFFICIENT_OPTION_HISTORY",))
    ce3, pe3, tgt = tuple(ce[-3:]), tuple(pe[-3:]), tuple(target[-5:])
    if not _same_contract(ce3, "CE") or not _same_contract(pe3, "PE") or not _same_contract(tgt, tgt[-1].option_type):
        return StrategyEvaluation(AlertState.WATCH, reasons=("CONTRACT_OR_EXPIRY_MISMATCH",))
    if ce3[-1].strike != pe3[-1].strike or ce3[-1].expiry != expiry or pe3[-1].expiry != expiry or tgt[-1].expiry != expiry:
        return StrategyEvaluation(AlertState.WATCH, reasons=("PAIR_NOT_SAME_STRIKE_EXPIRY",))
    for series in (ce3, pe3, tgt):
        if any(_structurally_valid(row) for row in series):
            return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_HISTORICAL_OBSERVATION",))
        times = [r.source_timestamp_ns for r in series]
        if any(b <= a for a, b in zip(times, times[1:])) or any(not 30*NS <= b-a <= 90*NS for a, b in zip(times, times[1:])):
            return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_OBSERVATION_CADENCE",))
    for row in (ce3[-1], pe3[-1], tgt[-1]):
        errors = _fresh(row, now_ns)
        if errors:
            return StrategyEvaluation(AlertState.WATCH, reasons=errors)
    if abs(ce3[-1].spot_price-pe3[-1].spot_price) > 5 or abs(tgt[-1].spot_price-ce3[-1].spot_price) > 5:
        return StrategyEvaluation(AlertState.WATCH, reasons=("UNSYNCHRONIZED_SPOT",))
    wanted, opposite = (ce3, pe3) if direction == "BULLISH" else (pe3, ce3)
    iv_jump = float(wanted[-1].iv)-float(wanted[0].iv)
    opposite_jump = float(opposite[-1].iv)-float(opposite[0].iv)
    threshold = 0.18 if direction == "BULLISH" else 0.20
    if iv_jump < threshold or opposite_jump > 0.05:
        return StrategyEvaluation(AlertState.WATCH, reasons=("IV_FLIP_NOT_CONFIRMED",))
    increments = [max(0, int(tgt[i].volume)-int(tgt[i-1].volume)) for i in range(1, 5)]
    baseline = sum(increments[:3])/3
    current_volume = increments[3]
    if baseline <= 0 or current_volume < 2.5*baseline:
        return StrategyEvaluation(AlertState.WATCH, reasons=("VOLUME_EXPANSION_MISSING",))
    premium_now, premium_prev = tgt[-1].ask, tgt[-2].ask
    if not (_positive(premium_now) and _positive(premium_prev) and premium_now > premium_prev):
        return StrategyEvaluation(AlertState.WATCH, reasons=("PREMIUM_NOT_RISING",))
    if tgt[-1].oi is None or tgt[-2].oi is None or tgt[-1].oi >= tgt[-2].oi:
        return StrategyEvaluation(AlertState.WATCH, reasons=("OI_NOT_DECREASING",))
    candidate = AlertCandidate(
        "IV_VOLUME_MOMENTUM", "NIFTY", expiry, tgt[-1].strike, direction,
        "CE" if direction == "BULLISH" else "PE", AlertState.EARLY_ENTRY, now_ns,
        tgt[-1].spot_price, float(premium_now), iv_jump, current_volume/baseline,
        "IV expansion, relative volume surge, rising premium and falling OI before confirmed breakout",
    )
    return StrategyEvaluation(AlertState.EARLY_ENTRY, candidate)


def even_strikes(strikes: Sequence[float]) -> tuple[float, ...]:
    """Return NIFTY alert strikes on the ₹100 grid (every second ₹50 strike)."""
    return tuple(sorted({float(s) for s in strikes if _positive(s) and float(s) % 100 == 0}))
