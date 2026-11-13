from dataclasses import replace
from app.scanner.iv_skew_flip.alerts.conditions import evaluate_strategy_a, even_strikes, NS
from app.scanner.iv_skew_flip.alerts.models import AlertState, OptionObservation, SpotBar


def observation(*, token, option_type, ts, iv, volume, oi=1000, premium=100.0):
    return OptionObservation(token, "NIFTY", "2026-11-19", 25000.0, option_type,
        ts, ts, ts, 25000.0, iv, "BID_ASK_MIDPOINT", premium-0.5, premium,
        premium, oi, ts, volume)


def setup():
    now = 1_800_000_000 * NS
    bars = [SpotBar(now-(15-i)*60*NS, 25000, 25010, 24990, 25000) for i in range(15)]
    ce_all = [observation(token="CE1", option_type="CE", ts=now-(4-i)*60*NS,
        iv=[10.0, 10.1, 10.15, 10.3, 10.4][i], volume=[100,110,120,130,500][i],
        oi=1000-i*10, premium=100+i) for i in range(5)]
    pe_all = [observation(token="PE1", option_type="PE", ts=now-(2-i)*60*NS,
        iv=[10.0,10.01,10.02][i], volume=100+i*10, premium=90) for i in range(3)]
    return now, bars, ce_all, pe_all


def test_strategy_a_emits_early_entry_without_breakout_close():
    now, bars, ce, pe = setup()
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=ce[-3:-1], pe=pe[-3:-1], target=ce[:4], direction="BULLISH",
        live_ce=ce[-1], live_pe=pe[-1], live_target=ce[-1],
    )
    assert result.state == AlertState.EARLY_ENTRY
    assert result.candidate is not None
    assert result.candidate.direction == "BULLISH"
    assert result.candidate.volume_ratio == 37.0


def test_strategy_a_uses_fresh_live_quote_not_stale_completed_minute_close():
    now, bars, ce, pe = setup()
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=ce[-3:-1], pe=pe[-3:-1], target=ce[:4], direction="BULLISH",
        live_ce=ce[-1], live_pe=pe[-1], live_target=ce[-1],
    )
    assert result.state == AlertState.EARLY_ENTRY


def test_strategy_a_rejects_stale_live_quote():
    now, bars, ce, pe = setup()
    stale_live = observation(
        token="CE1", option_type="CE", ts=now-6*NS,
        iv=10.4, volume=500, oi=960, premium=105,
    )
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=ce[-3:-1], pe=pe[-3:-1], target=ce[:4], direction="BULLISH",
        live_ce=stale_live, live_pe=pe[-1], live_target=stale_live,
    )
    assert result.state == AlertState.WATCH
    assert "STALE_OPTION_QUOTE" in result.reasons


def test_strategy_a_rejects_live_leg_that_reuses_last_completed_quote():
    now, bars, ce, pe = setup()
    # Simulate the first seconds after a minute boundary: the latest completed
    # CE observation is still within the five-second freshness window, but the
    # sampler has not received a newer exchange quote for that leg.
    ce_history = (
        replace(ce[-3], source_timestamp_ns=now-62*NS),
        replace(ce[-2], source_timestamp_ns=now-2*NS, spot_timestamp_ns=now,
                oi_timestamp_ns=now-2*NS),
    )
    repeated_live_ce = replace(
        ce[-2], source_timestamp_ns=now-2*NS, spot_timestamp_ns=now,
        oi_timestamp_ns=now-2*NS,
    )
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=ce_history, pe=pe[-3:-1], target=ce[:4], direction="BULLISH",
        live_ce=repeated_live_ce, live_pe=pe[-1], live_target=ce[-1],
    )
    assert result.state == AlertState.WATCH
    assert "LIVE_QUOTE_NOT_ADVANCED" in result.reasons


def test_even_strikes_includes_full_nifty_50_point_grid():
    assert even_strikes([24950, 25000, 25050, 25100, 25100, 25125]) == (
        24950.0, 25000.0, 25050.0, 25100.0,
    )


def test_strategy_a_rejects_oi_decline_without_a_new_broker_oi_observation():
    now, bars, ce, pe = setup()
    stale_oi_live = replace(ce[-1], oi_timestamp_ns=ce[-2].oi_timestamp_ns)
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=ce[-3:-1], pe=pe[-3:-1], target=ce[:4], direction="BULLISH",
        live_ce=ce[-1], live_pe=pe[-1], live_target=stale_oi_live,
    )
    assert result.state == AlertState.WATCH
    assert "LIVE_OI_NOT_UPDATED" in result.reasons


def test_strategy_a_rejects_stale_completed_spot_history():
    now, bars, ce, pe = setup()
    stale_bars = [
        replace(bar, timestamp_ns=bar.timestamp_ns-3*60*NS)
        for bar in bars
    ]
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=stale_bars,
        ce=ce[-3:-1], pe=pe[-3:-1], target=ce[:4], direction="BULLISH",
        live_ce=ce[-1], live_pe=pe[-1], live_target=ce[-1],
    )
    assert result.state == AlertState.WATCH
    assert "STALE_SPOT_HISTORY" in result.reasons


def test_strategy_a_rejects_ce_pe_history_from_different_minutes():
    now, bars, ce, pe = setup()
    shifted_pe = [
        replace(
            row,
            source_timestamp_ns=row.source_timestamp_ns-60*NS,
            oi_timestamp_ns=row.oi_timestamp_ns-60*NS,
        )
        for row in pe[-3:-1]
    ]
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=ce[-3:-1], pe=shifted_pe, target=ce[:4], direction="BULLISH",
        live_ce=ce[-1], live_pe=pe[-1], live_target=ce[-1],
    )
    assert result.state == AlertState.WATCH
    assert "UNALIGNED_OPTION_HISTORY" in result.reasons



def test_strategy_a_rejects_stale_historical_oi_even_when_quote_minutes_are_aligned():
    now, bars, ce, pe = setup()
    stale_target = [
        replace(row, oi_timestamp_ns=now-301*NS)
        for row in ce[:4]
    ]
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=ce[-3:-1], pe=pe[-3:-1], target=stale_target,
        direction="BULLISH", live_ce=ce[-1], live_pe=pe[-1],
        live_target=ce[-1],
    )
    assert result.state == AlertState.WATCH
    assert "STALE_HISTORICAL_OI" in result.reasons


def test_strategy_a_rejects_oi_timestamp_after_its_quote_timestamp():
    now, bars, ce, pe = setup()
    # Keep CE/PE/target source-minute buckets aligned so the evaluator
    # reaches structural timestamp validation instead of returning the earlier
    # UNALIGNED_OPTION_HISTORY guard.
    future_oi = replace(ce[2], oi_timestamp_ns=ce[2].source_timestamp_ns+1)
    result = evaluate_strategy_a(
        now_ns=now, expiry="2026-11-19", bars=bars,
        ce=(future_oi, ce[3]), pe=pe[-3:-1], target=ce[:4],
        direction="BULLISH", live_ce=ce[-1], live_pe=pe[-1],
        live_target=ce[-1],
    )
    assert result.state == AlertState.WATCH
    assert "INVALID_HISTORICAL_OBSERVATION" in result.reasons
