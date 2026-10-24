from app.scanner.weekly_swing_scanner import weekly_bars, indicators, analyze


def test_daily_rows_aggregate_into_weekly_bars():
    rows = [
        ["2026-10-19 09:15", "100", "105", "98", "103", "1000"],
        ["2026-10-20 09:15", "103", "108", "101", "106", "1200"],
        ["2026-10-26 09:15", "106", "110", "104", "108", "900"],
    ]
    bars = weekly_bars(rows)
    assert len(bars) == 2
    assert bars[0]["open"] == 100
    assert bars[0]["high"] == 108
    assert bars[0]["low"] == 98
    assert bars[0]["close"] == 106
    assert bars[0]["volume"] == 2200


def test_insufficient_history_never_fabricates_setups():
    bars = [{"week": f"2026-01-{i:02d}", "open": 100, "high": 105, "low": 95, "close": 101, "volume": 1000} for i in range(1, 20)]
    result = analyze("TEST", bars)
    assert result["status"] == "insufficient_data"
    assert result["matches"] == []


def test_indicators_return_aligned_series():
    bars = [{"week": f"2026-{i:02d}", "open": 100+i, "high": 103+i, "low": 98+i, "close": 101+i, "volume": 1000+i} for i in range(1, 31)]
    values = indicators(bars)
    assert all(len(values[key]) == len(bars) for key in ("cmf", "obv", "rsi", "mid", "lower", "upper"))
    assert values["lower"][-1] <= values["mid"][-1] <= values["upper"][-1]
