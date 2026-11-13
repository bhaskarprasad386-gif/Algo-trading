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
    if (row.oi is None or isinstance(row.oi, bool) or not isinstance(row.oi, int) or row.oi < 0
            or isinstance(row.oi_timestamp_ns, bool)
            or not isinstance(row.oi_timestamp_ns, int) or row.oi_timestamp_ns <= 0):
        errors.append("INVALID_OI")
    if row.volume is None or row.volume < 0: errors.append("INVALID_VOLUME")
    return tuple(errors)


def _same_contract(rows: Sequence[OptionObservation], option_type: str) -> bool:
    return bool(rows) and all(
        row.option_type == option_type and row.expiry == rows[0].expiry
        and row.strike == rows[0].strike and row.token == rows[0].token
        for row in rows
    )


def evaluate_strategy_a(*, now_ns: int, expiry: str, bars: Sequence[SpotBar],
                         ce: Sequence[OptionObservation], pe: Sequence[OptionObservation],
                         target: Sequence[OptionObservation], direction: str,
                         live_ce: OptionObservation, live_pe: OptionObservation,
                         live_target: OptionObservation) -> StrategyEvaluation:
    """Evaluate completed-minute history against a separately validated live quote.

    CE/PE use two completed minute observations plus the current live sample.
    Target volume uses four completed observations (three baseline increments)
    plus the current live cumulative volume increment.
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
    if any(not (_positive(b.open) and _positive(b.high) and _positive(b.low) and _positive(b.close))
           or b.high < max(b.open, b.close, b.low) or b.low > min(b.open, b.close, b.high)
           for b in last_bars):
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_SPOT_BAR",))
    if max(b.high for b in last_bars)-min(b.low for b in last_bars) > 55:
        return StrategyEvaluation(AlertState.WATCH, reasons=("NOT_CONSOLIDATING",))
    if len(ce) < 2 or len(pe) < 2 or len(target) < 4:
        return StrategyEvaluation(AlertState.WATCH, reasons=("INSUFFICIENT_OPTION_HISTORY",))

    ce3, pe3, tgt = tuple(ce[-2:]), tuple(pe[-2:]), tuple(target[-4:])
    if (not _same_contract(ce3, "CE") or not _same_contract(pe3, "PE")
            or not _same_contract(tgt, tgt[-1].option_type)):
        return StrategyEvaluation(AlertState.WATCH, reasons=("CONTRACT_OR_EXPIRY_MISMATCH",))
    live_rows = (live_ce, live_pe, live_target)
    for live, history in ((live_ce, ce3), (live_pe, pe3), (live_target, tgt)):
        if (live.token != history[-1].token or live.expiry != expiry
                or live.strike != history[-1].strike
                or live.option_type != history[-1].option_type):
            return StrategyEvaluation(AlertState.WATCH, reasons=("LIVE_CONTRACT_MISMATCH",))
    if (ce3[-1].strike != pe3[-1].strike or ce3[-1].expiry != expiry
            or pe3[-1].expiry != expiry or tgt[-1].expiry != expiry
            or ce3[-1].strike != tgt[-1].strike):
        return StrategyEvaluation(AlertState.WATCH, reasons=("PAIR_NOT_SAME_STRIKE_EXPIRY",))
    # Reject malformed observations before cadence checks so a corrupt
    # source/OI timestamp is never hidden behind a secondary alignment error.
    for series in (ce3, pe3, tgt):
        if any(_structurally_valid(row) for row in series):
            return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_HISTORICAL_OBSERVATION",))

    # Each feed is asynchronous. Compare ordered changes from each source;
    # do not require CE, PE, target and spot to share a minute or cadence.
    for series in (ce3, pe3, tgt):
        event_keys = [(row.source_timestamp_ns, int(row.oi_timestamp_ns or 0)) for row in series]
        if any(b <= a for a, b in zip(event_keys, event_keys[1:])):
            return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_OBSERVATION_ORDER",))
    for row, history in ((live_ce, ce3), (live_pe, pe3), (live_target, tgt)):
        if _structurally_valid(row):
            return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_LIVE_OBSERVATION",))
        if (row.source_timestamp_ns > now_ns or row.spot_timestamp_ns > now_ns
                or row.oi_timestamp_ns is None or row.oi_timestamp_ns > now_ns):
            return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_LIVE_TIMESTAMP",))
        # A recently sampled snapshot may still contain the exact last quote
        # from the completed minute. Freshness alone does not prove a new
        # exchange observation; require every live leg to advance its source
        # timestamp beyond its own completed-minute history.
        live_event = (row.source_timestamp_ns, int(row.oi_timestamp_ns or 0))
        prior_event = (history[-1].source_timestamp_ns, int(history[-1].oi_timestamp_ns or 0))
        if live_event <= prior_event:
            return StrategyEvaluation(AlertState.WATCH, reasons=("LIVE_OBSERVATION_NOT_ADVANCED",))
    # A quote refresh does not prove that OI itself was refreshed. The target
    # leg must carry a strictly newer broker OI timestamp than the completed
    # minute close before an OI-decrease condition can authorize an alert.
    if live_target.oi_timestamp_ns <= (tgt[-1].oi_timestamp_ns or 0):
        return StrategyEvaluation(AlertState.WATCH, reasons=("LIVE_OI_NOT_UPDATED",))
    if (abs(live_ce.spot_price-live_pe.spot_price) > 5
            or abs(live_target.spot_price-live_ce.spot_price) > 5):
        return StrategyEvaluation(AlertState.WATCH, reasons=("UNSYNCHRONIZED_SPOT",))

    wanted, opposite = ((ce3, live_ce), (pe3, live_pe)) if direction == "BULLISH" else ((pe3, live_pe), (ce3, live_ce))
    iv_jump = float(wanted[1].iv)-float(wanted[0][-2].iv)
    opposite_jump = float(opposite[1].iv)-float(opposite[0][-2].iv)
    threshold = 0.18 if direction == "BULLISH" else 0.20
    if iv_jump < threshold or opposite_jump > 0.05:
        return StrategyEvaluation(AlertState.WATCH, reasons=("IV_FLIP_NOT_CONFIRMED",))

    baseline_increments = [
        int(tgt[i].volume)-int(tgt[i-1].volume) for i in range(1, 4)
    ]
    current_increment = int(live_target.volume)-int(tgt[-1].volume)
    if any(delta < 0 for delta in baseline_increments) or current_increment < 0:
        return StrategyEvaluation(AlertState.WATCH, reasons=("CUMULATIVE_VOLUME_RESET",))
    baseline = sum(baseline_increments)/3
    if baseline <= 0 or current_increment < 2.5*baseline:
        return StrategyEvaluation(AlertState.WATCH, reasons=("VOLUME_EXPANSION_MISSING",))

    premium_now, premium_prev = live_target.ask, tgt[-1].ask
    if not (_positive(premium_now) and _positive(premium_prev) and premium_now > premium_prev):
        return StrategyEvaluation(AlertState.WATCH, reasons=("PREMIUM_NOT_RISING",))
    if live_target.oi is None or tgt[-1].oi is None or live_target.oi >= tgt[-1].oi:
        return StrategyEvaluation(AlertState.WATCH, reasons=("OI_NOT_DECREASING",))
    candidate = AlertCandidate(
        "IV_VOLUME_MOMENTUM", "NIFTY", expiry, live_target.strike, direction,
        "CE" if direction == "BULLISH" else "PE", AlertState.EARLY_ENTRY, now_ns,
        live_target.spot_price, float(premium_now), iv_jump,
        current_increment/baseline,
        "IV expansion, live-minute volume surge, rising premium and falling OI before confirmed breakout",
        {
            "checks": {
                "spot_bars_minimum": {"threshold": 15, "actual": len(last_bars), "passed": len(last_bars) >= 15},
                "spot_range_points": {"threshold_max": 55, "actual": max(b.high for b in last_bars)-min(b.low for b in last_bars), "passed": max(b.high for b in last_bars)-min(b.low for b in last_bars) <= 55},
                "option_history": {"threshold": "CE/PE 2 each; target 4", "actual": {"ce": len(ce3), "pe": len(pe3), "target": len(tgt)}, "passed": True},
                "change_timestamps_ns": {"ce_quote": live_ce.source_timestamp_ns, "pe_quote": live_pe.source_timestamp_ns, "target_quote": live_target.source_timestamp_ns, "ce_spot": live_ce.spot_timestamp_ns, "pe_spot": live_pe.spot_timestamp_ns, "target_spot": live_target.spot_timestamp_ns, "target_oi": live_target.oi_timestamp_ns},
                "iv_change_points": {"threshold_min": threshold, "actual": iv_jump, "opposite_max": 0.05, "opposite_actual": opposite_jump, "passed": iv_jump >= threshold and opposite_jump <= 0.05},
                "volume_expansion": {"threshold_min_ratio": 2.5, "baseline_increment": baseline, "live_increment": current_increment, "actual_ratio": current_increment/baseline, "passed": current_increment >= 2.5*baseline},
                "premium_rising": {"previous_ask": premium_prev, "live_ask": premium_now, "passed": premium_now > premium_prev},
                "oi_falling": {"previous": tgt[-1].oi, "live": live_target.oi, "passed": live_target.oi < tgt[-1].oi},
                "midpoint_iv": {"required_source": "BID_ASK_MIDPOINT", "actual": [row.iv_input for row in live_rows], "passed": True},
                "market_depth": {"required": "positive bid/ask; ask >= bid", "passed": True},
                "same_strike_expiry": {"strike": live_target.strike, "expiry": expiry, "passed": True},
            },
            "live_quotes": {
                "ce": {"bid": live_ce.bid, "ask": live_ce.ask, "iv": live_ce.iv, "oi": live_ce.oi, "volume": live_ce.volume},
                "pe": {"bid": live_pe.bid, "ask": live_pe.ask, "iv": live_pe.iv, "oi": live_pe.oi, "volume": live_pe.volume},
                "target": {"bid": live_target.bid, "ask": live_target.ask, "iv": live_target.iv, "oi": live_target.oi, "volume": live_target.volume},
            },
            "evaluated_at_ns": now_ns,
        },
    )
    return StrategyEvaluation(AlertState.EARLY_ENTRY, candidate)


def even_strikes(strikes: Sequence[float]) -> tuple[float, ...]:
    """Return valid NIFTY strikes on the exchange ₹50 grid."""
    return tuple(sorted({
        float(s) for s in strikes
        if _positive(s) and float(s) % 50 == 0
    }))


def evaluate_strategy_b(*, now_ns: int, expiry: str, spot_bars: Sequence[SpotBar],
                         ce_wall_history: Sequence[OptionObservation],
                         pe_wall_history: Sequence[OptionObservation],
                         entry_quote: OptionObservation,
                         direction: str) -> StrategyEvaluation:
    """Early OI-wall rejection trigger, before a full reversal is confirmed.

    For BULLISH, the PE-OI support wall must persist across three distinct
    one-minute observations; spot must probe within 15 points and print an
    initial bullish rejection candle. BEARISH mirrors this at the CE wall.
    The function deliberately emits an early alert on the first completed
    rejection candle; it does not wait for a second candle confirmation.
    """
    direction = direction.upper()
    if direction not in {"BULLISH", "BEARISH"}:
        return StrategyEvaluation(AlertState.INVALIDATED, reasons=("INVALID_DIRECTION",))
    wall_rows = tuple(pe_wall_history[-3:] if direction == "BULLISH" else ce_wall_history[-3:])
    if len(wall_rows) != 3 or len(spot_bars) < 2:
        return StrategyEvaluation(AlertState.WATCH, reasons=("INSUFFICIENT_WALL_OR_PRICE_HISTORY",))
    option_type = "PE" if direction == "BULLISH" else "CE"
    if not _same_contract(wall_rows, option_type):
        return StrategyEvaluation(AlertState.WATCH, reasons=("WALL_CONTRACT_SHIFTED",))
    if any(row.expiry != expiry for row in wall_rows):
        return StrategyEvaluation(AlertState.WATCH, reasons=("WALL_EXPIRY_MISMATCH",))
    for row in wall_rows:
        if _structurally_valid(row):
            return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_WALL_OBSERVATION",))
    event_keys = [(row.source_timestamp_ns, int(row.oi_timestamp_ns or 0)) for row in wall_rows]
    if any(b <= a for a, b in zip(event_keys, event_keys[1:])):
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_WALL_OBSERVATION_ORDER",))
    if len({row.strike for row in wall_rows}) != 1:
        return StrategyEvaluation(AlertState.WATCH, reasons=("WALL_NOT_PERSISTENT",))
    latest = wall_rows[-1]
    # No age gate: timestamps are for ordering and audit, not signal eligibility.
    if (latest.source_timestamp_ns > now_ns or latest.spot_timestamp_ns > now_ns
            or any(row.oi_timestamp_ns is None or row.oi_timestamp_ns > now_ns for row in wall_rows)):
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_WALL_TIMESTAMP",))
    wall_oi_timestamps = [row.oi_timestamp_ns for row in wall_rows]
    if any(
        current is None or previous is None or current <= previous
        for previous, current in zip(wall_oi_timestamps, wall_oi_timestamps[1:])
    ):
        return StrategyEvaluation(AlertState.WATCH, reasons=("WALL_OI_TIMESTAMP_NOT_ADVANCING",))
    entry_type = "CE" if direction == "BULLISH" else "PE"
    if (entry_quote.underlying.upper() != "NIFTY" or entry_quote.option_type != entry_type
            or entry_quote.expiry != expiry or entry_quote.strike != latest.strike):
        return StrategyEvaluation(AlertState.WATCH, reasons=("ENTRY_CONTRACT_MISMATCH",))
    if _structurally_valid(entry_quote):
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_ENTRY_OBSERVATION",))
    if (entry_quote.source_timestamp_ns > now_ns or entry_quote.spot_timestamp_ns > now_ns
            or entry_quote.oi_timestamp_ns is None or entry_quote.oi_timestamp_ns > now_ns):
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_ENTRY_TIMESTAMP",))
    if abs(entry_quote.spot_price-latest.spot_price) > 5:
        return StrategyEvaluation(AlertState.WATCH, reasons=("UNSYNCHRONIZED_ENTRY_SPOT",))
    if any(row.oi is None for row in wall_rows) or not (wall_rows[0].oi < wall_rows[1].oi < wall_rows[2].oi):
        return StrategyEvaluation(AlertState.WATCH, reasons=("WALL_OI_NOT_RISING",))
    iv_change = float(wall_rows[-1].iv)-float(wall_rows[-2].iv)
    if iv_change > -0.10:
        return StrategyEvaluation(AlertState.WATCH, reasons=("WALL_IV_NOT_COOLING",))
    bars = tuple(spot_bars[-2:])
    if any(bar.timestamp_ns <= 0 or now_ns < bar.timestamp_ns
           or not (_positive(bar.open) and _positive(bar.high) and _positive(bar.low) and _positive(bar.close))
           or bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high)
           for bar in bars) or bars[-1].timestamp_ns <= bars[-2].timestamp_ns:
        return StrategyEvaluation(AlertState.WATCH, reasons=("INVALID_PRICE_BAR_ORDER",))
    prior, current = bars
    wall = latest.strike
    if direction == "BEARISH":
        # Probe the CE resistance, reject it and close back below the wall.
        near_wall = current.high >= wall and current.high-wall <= 15
        rejection = current.close < current.open and current.close < prior.close and current.close < wall
        reason = "CE OI resistance rejected; PE early entry"
    else:
        # Probe the PE support, reject it and close back above the wall.
        near_wall = current.low <= wall and wall-current.low <= 15
        rejection = current.close > current.open and current.close > prior.close and current.close > wall
        reason = "PE OI support defended; CE early entry"
    if not near_wall:
        return StrategyEvaluation(AlertState.WATCH, reasons=("SPOT_NOT_AT_WALL",))
    if not rejection:
        return StrategyEvaluation(AlertState.WATCH, reasons=("INITIAL_REJECTION_NOT_VISIBLE",))
    candidate = AlertCandidate(
        "OI_WALL_REJECTION", "NIFTY", expiry, wall, direction,
        "CE" if direction == "BULLISH" else "PE", AlertState.EARLY_ENTRY,
        now_ns, current.close, entry_quote.ask, iv_change, None, reason,
        {
            "wall_oi": latest.oi, "wall_strike": wall, "wall_option_type": option_type,
            "entry_quote": {"token": entry_quote.token, "option_type": entry_quote.option_type,
                            "bid": entry_quote.bid, "ask": entry_quote.ask,
                            "source_timestamp_ns": entry_quote.source_timestamp_ns},
            "checks": {
                "persistent_wall": {"required_observations": 3, "actual": len(wall_rows), "strike_history": [row.strike for row in wall_rows], "passed": len(wall_rows) == 3 and len({row.strike for row in wall_rows}) == 1},
                "wall_oi_rising": {"values": [row.oi for row in wall_rows], "passed": wall_rows[0].oi < wall_rows[1].oi < wall_rows[2].oi},
                "wall_iv_cooling": {"threshold_max_change_points": -0.10, "actual_change_points": iv_change, "previous_iv": wall_rows[-2].iv, "live_iv": latest.iv, "passed": iv_change <= -0.10},
                "spot_distance_to_wall": {"threshold_max_points": 15, "actual_points": (wall-current.low) if direction == "BULLISH" else (current.high-wall), "wall_strike": wall, "passed": near_wall},
                "initial_rejection_candle": {"open": current.open, "high": current.high, "low": current.low, "close": current.close, "previous_close": prior.close, "passed": rejection},
                "change_timestamps_ns": {"wall_source": latest.source_timestamp_ns, "wall_oi": latest.oi_timestamp_ns, "wall_spot": latest.spot_timestamp_ns, "entry_quote": entry_quote.source_timestamp_ns, "entry_spot": entry_quote.spot_timestamp_ns, "entry_oi": entry_quote.oi_timestamp_ns, "rejection_candle": current.timestamp_ns},
                "same_expiry_contract": {"expiry": expiry, "strike": wall, "option_type": option_type, "passed": True},
            },
            "evaluated_at_ns": now_ns,
        },
    )
    return StrategyEvaluation(AlertState.EARLY_ENTRY, candidate)
