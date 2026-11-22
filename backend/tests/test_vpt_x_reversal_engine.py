import pytest
import time
from app.scanner.vpt_x_reversal_engine import evaluate_vptx


def _ticks(prices, start=None):
    start = start if start is not None else int(time.time() * 1000) - 3_000
    return [{"timestamp_ms":start+i*100,"price":p,"buy_volume":30+i*2,"sell_volume":20,
             "bid_qty":120+i*3,"ask_qty":60,"bid":p-.05,"ask":p+.05} for i,p in enumerate(prices)]


@pytest.mark.parametrize("index",["NIFTY","BANKNIFTY"])
def test_supported_indices_evaluate_both_directions_and_three_patterns(index):
    result=evaluate_vptx({"underlying":index,"ticks":_ticks([100+i*.1 for i in range(30)])})
    assert result["status"]=="EVALUATED"
    assert len(result["candidates"])==6
    assert {c["direction"] for c in result["candidates"]}=={"BUY","SELL"}
    assert {c["pattern"] for c in result["candidates"]}=={"SWING_EXHAUSTION","COMPRESSION_REVERSAL","V_REVERSAL"}
    assert result["scanner_only"] and not result["order_routing_enabled"]
    assert not result["subscription_enabled"]


def test_discontinuous_tick_history_fails_closed_even_when_last_tick_is_fresh():
    start = int(time.time() * 1000) - 6_000
    ticks = _ticks([100 + i * .1 for i in range(30)], start=start)
    # A 3-second gap is beyond the allowed 2.5-second continuity window,
    # while the final observation remains fresh.
    for tick in ticks[15:]:
        tick["timestamp_ms"] += 3_000
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": ticks})
    assert result["status"] == "WAITING_DATA"
    assert result["reason"] == "DISCONTINUOUS_TICK_HISTORY"
    assert result["candidates"] == []

def test_sweep_invalidation_uses_recent_sweep_extreme_not_window_low_high():
    import app.scanner.vpt_x_reversal_engine as engine
    ticks = _ticks([100 + i * .1 for i in range(30)])
    metrics = engine._metrics(engine._normalize_ticks(ticks))
    assert metrics["buy_invalidation"] == pytest.approx(metrics["low"])
    assert metrics["sell_invalidation"] == pytest.approx(metrics["high"])

    # Recent terminal segment sweeps prior low, then reclaims it.
    start = int(time.time() * 1000) - 3_000
    prices = [100 + i * .05 for i in range(20)] + [101, 100, 99.5, 99, 98.5, 99, 100, 100.5, 101, 101.2]
    swept = _ticks(prices, start=start)
    swept_metrics = engine._metrics(engine._normalize_ticks(swept))
    assert swept_metrics["swept_low"] is True
    assert swept_metrics["reclaimed_low"] is True
    assert swept_metrics["buy_invalidation"] == pytest.approx(min(p["price"] for p in swept[-5:]))

def test_stale_tick_data_fails_closed():
    result=evaluate_vptx({"underlying":"NIFTY","ticks":_ticks([100+i*.1 for i in range(25)], start=1_000_000)},now_ms=1_010_000)
    assert result["status"]=="WAITING_DATA" and result["candidates"]==[]


def test_tick_input_is_bounded_to_latest_500_observations():
    start = int(time.time() * 1000) - 3_500
    ticks = [{"timestamp_ms": start + i * 5, "price": 100 + i * .01,
              "buy_volume": 30, "sell_volume": 20, "bid_qty": 80, "ask_qty": 50}
             for i in range(600)]
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": ticks})
    assert result["status"] == "EVALUATED"
    assert result["tick_count"] == 500


def test_unordered_input_keeps_chronologically_latest_500_ticks():
    start = int(time.time() * 1000) - 4_000
    ticks = [{"timestamp_ms": start + i * 5, "price": 100 + i * .01,
              "buy_volume": 30, "sell_volume": 20, "bid_qty": 80, "ask_qty": 50}
             for i in range(600)]
    # Put oldest 100 observations last; timestamp-based truncation should still keep latest 500.
    ticks = ticks[100:] + ticks[:100]
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": ticks})
    assert result["status"] == "EVALUATED"
    assert result["tick_count"] == 500
    assert result["features"]["low"] == pytest.approx(101.00)
    assert result["features"]["price"] == pytest.approx(105.99)



def test_future_tick_timestamps_fail_closed():
    future_start = int(time.time() * 1000) + 10_000
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": _ticks([100+i*.1 for i in range(30)], start=future_start)})
    assert result["status"] == "WAITING_DATA" and result["candidates"] == []


def test_nonfinite_ticks_are_filtered():
    raw = _ticks([100+i*.1 for i in range(30)])
    raw.insert(3, {"timestamp_ms": raw[3]["timestamp_ms"] + 1, "price": float("nan")})
    result = evaluate_vptx({"underlying": "BANKNIFTY", "ticks": raw})
    assert result["status"] == "EVALUATED" and result["tick_count"] == 30


def test_short_or_bad_tick_series_fails_closed():
    assert evaluate_vptx({"underlying":"BANKNIFTY","ticks":_ticks([100,101,102])})["status"]=="WAITING_DATA"


def test_unsupported_underlying_rejected():
    result=evaluate_vptx({"underlying":"FINNIFTY","ticks":_ticks([100+i*.1 for i in range(30)])})
    assert result["status"]=="REJECTED" and result["reason"]=="UNSUPPORTED_UNDERLYING"


def test_invalid_and_duplicate_timestamps_are_cleaned():
    raw=_ticks([100+i*.1 for i in range(30)])
    raw.append({"timestamp_ms":raw[-1]["timestamp_ms"],"price":raw[-1]["price"],"buy_volume":3})
    result=evaluate_vptx({"underlying":"NIFTY","ticks":raw})
    assert result["status"]=="EVALUATED" and result["tick_count"]==30


def test_duplicate_bursts_do_not_evict_unique_ticks_before_cap():
    start = int(time.time() * 1000) - 3_500
    ticks = [{"timestamp_ms": start + i * 5, "price": 100 + i * .01,
              "buy_volume": 30, "sell_volume": 20, "bid_qty": 80, "ask_qty": 50}
             for i in range(600)]
    ticks.extend(ticks[-100:])
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": ticks})
    assert result["status"] == "EVALUATED"
    assert result["tick_count"] == 500
    assert result["features"]["low"] == pytest.approx(101.00)
    assert result["features"]["price"] == pytest.approx(105.99)


def test_signal_never_routes_orders_even_with_cross_confirmation():
    result=evaluate_vptx({"underlying":"BANKNIFTY","ticks":_ticks([100+i*.1 for i in range(30)]),"cross_confirmation":.9})
    assert all(c["order_routing_enabled"] is False for c in result["candidates"])
    assert result["subscription_enabled"] is False


def test_monotonic_trend_does_not_trigger_reversal_by_itself():
    result=evaluate_vptx({"underlying":"NIFTY","ticks":_ticks([100+i*.1 for i in range(30)])})
    assert result["status"]=="EVALUATED"
    assert all(c["trigger_ready"] is False for c in result["candidates"])


def test_missing_tick_array_is_waiting_not_exception():
    result=evaluate_vptx({"underlying":"BANKNIFTY","ticks":None})
    assert result["status"]=="WAITING_DATA"
    assert result["candidates"]==[]


def test_old_ticks_are_stale_against_real_server_clock():
    result=evaluate_vptx({"underlying":"NIFTY","ticks":_ticks([100+i*.1 for i in range(30)], start=1_000_000)})
    assert result["status"]=="WAITING_DATA" and result["candidates"]==[]


def test_zero_baseline_impact_does_not_create_decay_signal(monkeypatch):
    import app.scanner.vpt_x_reversal_engine as engine
    monkeypatch.setattr(engine, "_impact", lambda ticks, side: 0.0)
    ticks = _ticks([100 + i * .01 for i in range(30)])
    normalized = engine._normalize_ticks(ticks)
    metrics = engine._metrics(normalized)
    assert metrics["sell_impact_decay"] == 0.0
    assert metrics["buy_impact_decay"] == 0.0


def test_cross_confirmation_bonus_updates_strength_threshold(monkeypatch):
    import app.scanner.vpt_x_reversal_engine as engine
    monkeypatch.setattr(engine, "_score", lambda direction, metrics, pattern: (70, {"base": 70}, []))
    ticks = _ticks([100 + i * .1 for i in range(30)])
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": ticks, "cross_confirmation": .9})
    assert result["status"] == "EVALUATED"
    assert all(candidate["score"] == 75 for candidate in result["candidates"])
    assert all(candidate["strength"] == "STRONG" for candidate in result["candidates"])


def test_cross_confirmation_bonus_keeps_score_components_normalized(monkeypatch):
    import app.scanner.vpt_x_reversal_engine as engine
    monkeypatch.setattr(engine, "_score", lambda direction, metrics, pattern: (98, {"base": 98}, []))
    ticks = _ticks([100 + i * .1 for i in range(30)])
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": ticks, "cross_confirmation": .9})
    assert result["status"] == "EVALUATED"
    for candidate in result["candidates"]:
        assert candidate["score"] == 100
        assert sum(candidate["components"].values()) == candidate["score"]
        assert candidate["components"]["cross_market_confirmation"] == 2
