from app.scanner.iv_skew_flip.alerts.conditions import evaluate_strategy_b, NS
from app.scanner.iv_skew_flip.alerts.models import AlertState, OptionObservation, SpotBar


def wall_observation(*, token, option_type, ts, strike, iv, oi):
    return OptionObservation(token, "NIFTY", "2026-11-19", strike, option_type,
        ts, ts, ts, 25000.0, iv, "BID_ASK_MIDPOINT", 90.0, 91.0,
        90.5, oi, ts, 1000)


def test_strategy_b_early_support_bounce_before_second_candle_confirmation():
    now = 1_800_000_000 * NS
    wall = [wall_observation(token="PE25000", option_type="PE", ts=now-(2-i)*60*NS,
        strike=25000, iv=[12.0, 11.95, 11.80][i], oi=[10000, 10100, 10300][i]) for i in range(3)]
    bars = [
        SpotBar(now-60*NS, 24990, 25002, 24980, 24995),
        SpotBar(now, 24990, 25010, 24988, 25005),
    ]
    entry = wall_observation(token="CE25000", option_type="CE", ts=now,
        strike=25000, iv=12.1, oi=9000)
    entry = OptionObservation(entry.token, entry.underlying, entry.expiry, entry.strike,
        entry.option_type, entry.source_timestamp_ns, entry.sampled_at_ns,
        entry.spot_timestamp_ns, entry.spot_price, entry.iv, entry.iv_input,
        99.0, 101.0, 100.0, entry.oi, entry.oi_timestamp_ns, entry.volume)
    result = evaluate_strategy_b(now_ns=now, expiry="2026-11-19",
        spot_bars=bars, ce_wall_history=[], pe_wall_history=wall,
        entry_quote=entry, direction="BULLISH")
    assert result.state == AlertState.EARLY_ENTRY
    assert result.candidate is not None
    assert result.candidate.option_type == "CE"
    assert result.candidate.premium == 101.0
    change_times = result.candidate.metadata["checks"]["change_timestamps_ns"]
    assert change_times["wall_source"] == wall[-1].source_timestamp_ns
    assert change_times["entry_quote"] == entry.source_timestamp_ns


def test_strategy_b_rejects_wall_that_shifts_strike():
    now = 1_800_000_000 * NS
    wall = [wall_observation(token="PE25000", option_type="PE", ts=now-(2-i)*60*NS,
        strike=25000+i*50, iv=11.0-i*.1, oi=10000+i*100) for i in range(3)]
    bars = [SpotBar(now-60*NS, 24990, 25002, 24980, 24995),
            SpotBar(now, 24990, 25010, 24988, 25005)]
    entry = wall_observation(token="CE25000", option_type="CE", ts=now,
        strike=25000, iv=12.1, oi=9000)
    result = evaluate_strategy_b(now_ns=now, expiry="2026-11-19",
        spot_bars=bars, ce_wall_history=[], pe_wall_history=wall,
        entry_quote=entry, direction="BULLISH")
    assert result.state == AlertState.WATCH
    assert "WALL_CONTRACT_SHIFTED" in result.reasons


def test_strategy_b_rejects_repeated_oi_observation_timestamps():
    from dataclasses import replace

    now = 1_800_000_000 * NS
    wall = [wall_observation(token="PE25000", option_type="PE", ts=now-(2-i)*60*NS,
        strike=25000, iv=[12.0, 11.95, 11.80][i], oi=[10000, 10100, 10300][i]) for i in range(3)]
    wall[-1] = replace(wall[-1], oi_timestamp_ns=wall[-2].oi_timestamp_ns)
    bars = [
        SpotBar(now-60*NS, 24990, 25002, 24980, 24995),
        SpotBar(now, 24990, 25010, 24988, 25005),
    ]
    entry = wall_observation(token="CE25000", option_type="CE", ts=now,
        strike=25000, iv=12.1, oi=9000)
    entry = replace(entry, bid=99.0, ask=101.0, ltp=100.0)
    result = evaluate_strategy_b(now_ns=now, expiry="2026-11-19",
        spot_bars=bars, ce_wall_history=[], pe_wall_history=wall,
        entry_quote=entry, direction="BULLISH")
    assert result.state == AlertState.WATCH
    assert "WALL_OI_TIMESTAMP_NOT_ADVANCING" in result.reasons



def test_strategy_b_accepts_fresh_completed_wall_bucket_older_than_live_quote_window():
    from dataclasses import replace

    now = 1_800_000_000 * NS
    wall = [wall_observation(token="PE25000", option_type="PE", ts=now-(2-i)*60*NS,
        strike=25000, iv=[12.0, 11.95, 11.80][i], oi=[10000, 10100, 10300][i]) for i in range(3)]
    # The newest completed minute observation is 30 seconds old. It is valid
    # history, but must not be subjected to the <=5s live-quote freshness gate.
    wall[-1] = replace(
        wall[-1],
        source_timestamp_ns=now-30*NS,
        sampled_at_ns=now-30*NS,
        spot_timestamp_ns=now-30*NS,
        oi_timestamp_ns=now-30*NS,
    )
    bars = [
        SpotBar(now-60*NS, 24990, 25002, 24980, 24995),
        SpotBar(now, 24990, 25010, 24988, 25005),
    ]
    entry = wall_observation(token="CE25000", option_type="CE", ts=now,
        strike=25000, iv=12.1, oi=9000)
    entry = replace(entry, bid=99.0, ask=101.0, ltp=100.0)
    result = evaluate_strategy_b(
        now_ns=now, expiry="2026-11-19", spot_bars=bars,
        ce_wall_history=[], pe_wall_history=wall,
        entry_quote=entry, direction="BULLISH",
    )
    assert result.state == AlertState.EARLY_ENTRY



def test_strategy_b_accepts_oi_observations_without_fixed_age_limit():
    from dataclasses import replace

    now = 1_800_000_000 * NS
    wall = [wall_observation(token="PE25000", option_type="PE", ts=now-(2-i)*60*NS,
        strike=25000, iv=[12.0, 11.95, 11.80][i], oi=[10000, 10100, 10300][i]) for i in range(3)]
    # The earliest observation is more than five minutes old. Age is not
    # a signal gate; source ordering and the OI pattern remain mandatory.
    wall[0] = replace(wall[0], oi_timestamp_ns=now-301*NS)
    bars = [
        SpotBar(now-60*NS, 24990, 25002, 24980, 24995),
        SpotBar(now, 24990, 25010, 24988, 25005),
    ]
    entry = wall_observation(token="CE25000", option_type="CE", ts=now,
        strike=25000, iv=12.1, oi=9000)
    entry = replace(entry, bid=99.0, ask=101.0, ltp=100.0)
    result = evaluate_strategy_b(
        now_ns=now, expiry="2026-11-19", spot_bars=bars,
        ce_wall_history=[], pe_wall_history=wall,
        entry_quote=entry, direction="BULLISH",
    )
    assert result.state == AlertState.EARLY_ENTRY
