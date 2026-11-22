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


def test_stale_tick_data_fails_closed():
    result=evaluate_vptx({"underlying":"NIFTY","ticks":_ticks([100+i*.1 for i in range(25)], start=1_000_000)},now_ms=1_010_000)
    assert result["status"]=="WAITING_DATA" and result["candidates"]==[]


def test_tick_input_is_bounded_to_latest_500_observations():
    start = int(time.time() * 1000) - 3_000
    ticks = _ticks([100 + i * .01 for i in range(600)], start=start)
    result = evaluate_vptx({"underlying": "NIFTY", "ticks": ticks})
    assert result["status"] == "EVALUATED"
    assert result["tick_count"] == 500


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
