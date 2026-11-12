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
    result = evaluate_strategy_b(now_ns=now, expiry="2026-11-19",
        spot_bars=bars, ce_wall_history=[], pe_wall_history=wall, direction="BULLISH")
    assert result.state == AlertState.EARLY_ENTRY
    assert result.candidate is not None
    assert result.candidate.option_type == "CE"


def test_strategy_b_rejects_wall_that_shifts_strike():
    now = 1_800_000_000 * NS
    wall = [wall_observation(token="PE25000", option_type="PE", ts=now-(2-i)*60*NS,
        strike=25000+i*50, iv=11.0-i*.1, oi=10000+i*100) for i in range(3)]
    bars = [SpotBar(now-60*NS, 24990, 25002, 24980, 24995),
            SpotBar(now, 24990, 25010, 24988, 25005)]
    result = evaluate_strategy_b(now_ns=now, expiry="2026-11-19",
        spot_bars=bars, ce_wall_history=[], pe_wall_history=wall, direction="BULLISH")
    assert result.state == AlertState.WATCH
    assert "WALL_CONTRACT_SHIFTED" in result.reasons
