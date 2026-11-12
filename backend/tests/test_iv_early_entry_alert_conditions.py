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
        ce=ce[-3:], pe=pe, target=ce[:4], direction="BULLISH",
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
        ce=ce[-3:], pe=pe, target=ce[:4], direction="BULLISH",
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
        ce=ce[-3:], pe=pe, target=ce[:4], direction="BULLISH",
        live_ce=stale_live, live_pe=pe[-1], live_target=stale_live,
    )
    assert result.state == AlertState.WATCH
    assert "STALE_OPTION_QUOTE" in result.reasons


def test_even_strikes_uses_100_point_grid():
    assert even_strikes([24950, 25000, 25050, 25100, 25100]) == (25000.0, 25100.0)
