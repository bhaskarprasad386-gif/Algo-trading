"""Pure early-entry condition evaluators. No I/O, broker calls, or order placement."""
from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

from .models import AlertCandidate, AlertState, OptionObservation, SpotBar, StrategyEvaluation

NS = 1_000_000_000


def _finite_positive(value: float | int | None) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def valid_observation(row: OptionObservation, now_ns: int, *, max_quote_age_s: int = 5,
                      max_spot_age_s: int = 5, max_oi_age_s: int = 300) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if not row.token or not row.expiry or row.underlying.upper() != "NIFTY": reasons.append("WRONG_UNIVERSE")
    if row.option_type not in {"CE", "PE"}: reasons.append("INVALID_OPTION_TYPE")
    if row.source_timestamp_ns <= 0 or now_ns < row.source_timestamp_ns or now_ns-row.source_timestamp_ns > max_quote_age_s*NS: reasons.append("STALE_OPTION_QUOTE")
    if row.spot_timestamp_ns <= 0 or now_ns < row.spot_timestamp_ns or now_ns-row.spot_timestamp_ns > max_spot_age_s*NS: reasons.append("STALE_SPOT")
    if not _finite_positive(row.spot_price): reasons.append("INVALID_SPOT")
    if not _finite_positive(row.iv): reasons.append("INVALID_IV")
    if row.iv_input != "BID_ASK_MIDPOINT": reasons.append("IV_NOT_BID_ASK_MIDPOINT")
    if not (_finite_positive(row.bid) and _finite_positive(row.ask) and row.ask >= row.bid): reasons.append("INVALID_MARKET_DEPTH")
    if row.oi is None or row.oi < 0 or row.oi_timestamp_ns is None or row.oi_timestamp_ns <= 0 or now_ns < row.oi_timestamp_ns or now_ns-row.oi_timestamp_ns > max_oi_age_s*NS: reasons.append("STALE_OI")
    if row.volume is None or row.volume < 0: reasons.append("INVALID_VOLUME")
    return not reasons, tuple(reasons)


def _same_contract(rows: Sequence[OptionObservation], option_type: str) -> bool:
    return bool(rows) and all(
        row.option_type == option_type and row.expiry == rows[0].expiry
        and row.strike == rows[0].strike and row.token == rows[0].token
        for row in rows
    )


def evaluate_strategy_a(*, now_ns: int, expiry: str, bars: Sequence[SpotBar],
                         ce: Sequence[OptionObservation], pe: Sequence[OptionObservation],
                         target: Sequence[OptionObservation], direction: str) -> StrategyEvaluation:
    """Early IV/volume momentum trigger; intentionally does not wait for breakout close.

    Caller supplies chronological one-minute bars and 1-second observations. The
    final three target observations must be consecutive, distinct source ticks.
    """
    direction = direction.upper()
    if direction not in {"BULLISH", "BEARISH"}: return StrategyEvaluation(AlertState.INVALIDATED, reasons=("INVALID_DIRECTION",))
    if len(bars) < 15: return StrategyEvaluation(AlertState.WATCH, reasons=("INSUFFICIENT_SPOT_BUCKETS",))
    last_bars = tuple(bars[-15:])
    stamps = [b.timestamp_ns for b in last_bars]
    if any(b.timestamp_ns <= 0 for b in last_bars) or any(b <= a for a,b in zip(stamps, stamps[1:])):
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_SPOT_BUCKET_ORDER",))
    if any((b-a) > 90*NS or (b-a) < 30*NS for a,b in zip(stamps, stamps[1:])):
        return StrategyEvaluation(AlertState.WATCH, reasons=("MISSING_SPOT_BUCKET",))
    if max(b.high for b in last_bars)-min(b.low for b in last_bars) > 55:
        return StrategyEvaluation(AlertState.WATCH, reasons=("NOT_CONSOLIDATING",))
    if len(ce) < 3 or len(pe) < 3 or len(target) < 4:
        return StrategyEvaluation(AlertState.WATCH, reasons=("INSUFFICIENT_OPTION_HISTORY",))
    ce3, pe3, tgt = tuple(ce[-3:]), tuple(pe[-3:]), tuple(target[-4:])
    if not _same_contract(ce3, "CE") or not _same_contract(pe3, "PE") or not _same_contract(tgt, tgt[-1].option_type):
        return StrategyEvaluation(AlertState.WATCH, reasons=("CONTRACT_OR_EXPIRY_MISMATCH",))
    if ce3[-1].strike != pe3[-1].strike or ce3[-1].expiry != expiry or pe3[-1].expiry != expiry or tgt[-1].expiry != expiry:
        return StrategyEvaluation(AlertState.WATCH, reasons=("PAIR_NOT_SAME_STRIKE_EXPIRY",))
    for row in (*ce3, *pe3, *tgt):
        ok, why = valid_observation(row, now_ns)
        if not ok: return StrategyEvaluation(AlertState.WATCH, reasons=why)
    if len({r.source_timestamp_ns for r in tgt}) != len(tgt) or any(b.source_timestamp_ns <= a.source_timestamp_ns for a,b in zip(tgt,tgt[1:])):
        return StrategyEvaluation(AlertState.WATCH, reasons=("NON_DISTINCT_TARGET_TICKS",))
    if any(abs(r.spot_price-tgt[-1].spot_price) > 10 for r in (*ce3,*pe3)):
        return StrategyEvaluation(AlertState.WATCH, reasons=("UNSYNCHRONIZED_SPOT",))
    wanted, opposite = (ce3, pe3) if direction == "BULLISH" else (pe3, ce3)
    threshold = 0.18 if direction == "BULLISH" else 0.20
    iv_jump = wanted[-1].iv - wanted[0].iv
    opposite_jump = opposite[-1].iv - opposite[0].iv
    if iv_jump < threshold or opposite_jump > 0.05:
        return StrategyEvaluation(AlertState.WATCH, reasons=("IV_FLIP_NOT_CONFIRMED",))
    vols = [max(0, tgt[i].volume-tgt[i-1].volume) for i in range(1,4)]
    baseline = sum(vols[:2])/2
    current_volume = vols[-1]
    if baseline <= 0 or current_volume < 2.5*baseline:
        return StrategyEvaluation(AlertState.WATCH, reasons=("VOLUME_EXPANSION_MISSING",))
    premium_now = tgt[-1].ask if tgt[-1].ask is not None else tgt[-1].ltp
    premium_prev = tgt[-2].ask if tgt[-2].ask is not None else tgt[-2].ltp
    if not (_finite_positive(premium_now) and _finite_positive(premium_prev) and premium_now > premium_prev):
        return StrategyEvaluation(AlertState.WATCH, reasons=("PREMIUM_NOT_RISING",))
    if tgt[-1].oi is None or tgt[-2].oi is None or tgt[-1].oi >= tgt[-2].oi:
        return StrategyEvaluation(AlertState.WATCH, reasons=("OI_NOT_DECREASING",))
    candidate = AlertCandidate("IV_VOLUME_MOMENTUM", "NIFTY", expiry, tgt[-1].strike, direction,
        "CE" if direction == "BULLISH" else "PE", AlertState.EARLY_ENTRY, now_ns,
        tgt[-1].spot_price, float(premium_now), float(iv_jump), current_volume/max(baseline,1),
        "IV expansion, relative volume surge, rising premium and falling OI before confirmed breakout")
    return StrategyEvaluation(AlertState.EARLY_ENTRY, candidate)


def even_strikes(strikes: Sequence[float]) -> tuple[float, ...]:
    """Return NIFTY alert strikes on the ₹100 grid (every second ₹50 strike)."""
    return tuple(sorted({float(s) for s in strikes if _finite_positive(s) and float(s) % 100 == 0}))
