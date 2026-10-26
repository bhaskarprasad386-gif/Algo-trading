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


def test_incomplete_current_week_is_excluded_from_pivot_confirmation():
    from datetime import date
    from app.scanner.weekly_swing_scanner import completed_weekly_bars

    rows = [
        ["2026-10-19 09:15", "100", "105", "98", "103", "1000"],
        ["2026-10-20 09:15", "103", "108", "101", "106", "1200"],
        ["2026-10-21 09:15", "106", "110", "104", "108", "900"],
        ["2026-10-22 09:15", "108", "111", "105", "109", "800"],
    ]
    bars = completed_weekly_bars(rows, date(2026, 10, 22))
    assert bars == []


def test_current_week_is_included_after_its_final_trading_session():
    from datetime import date
    from app.scanner.weekly_swing_scanner import completed_weekly_bars

    rows = [
        ["2026-10-19 09:15", "100", "105", "98", "103", "1000"],
        ["2026-10-20 09:15", "103", "108", "101", "106", "1200"],
        ["2026-10-21 09:15", "106", "110", "104", "108", "900"],
        ["2026-10-22 09:15", "108", "111", "105", "109", "800"],
        ["2026-10-23 09:15", "109", "112", "106", "110", "700"],
    ]
    bars = completed_weekly_bars(rows, date(2026, 10, 23))
    assert len(bars) == 1
    assert bars[0]["week"] == "2026-10-19"
    assert bars[0]["close"] == 110


def test_holiday_shortened_week_completes_on_last_trading_day():
    from datetime import date
    from app.scanner.weekly_swing_scanner import completed_weekly_bars

    # Friday 2026-10-02 is in the scanner's NSE holiday snapshot.
    rows = [
        ["2026-09-28 09:15", "100", "105", "98", "103", "1000"],
        ["2026-09-29 09:15", "103", "108", "101", "106", "1200"],
        ["2026-09-30 09:15", "106", "110", "104", "108", "900"],
        ["2026-10-01 09:15", "108", "111", "105", "109", "800"],
    ]
    bars = completed_weekly_bars(rows, date(2026, 10, 1))
    assert len(bars) == 1
    assert bars[0]["week"] == "2026-09-28"


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


def test_rsi_uses_wilder_smoothing_after_initial_14_changes():
    from app.scanner.weekly_swing_scanner import indicators

    closes = [100 + i for i in range(15)] + [113]
    bars = [
        {"week": f"2026-W{i:02d}", "open": close, "high": close + 1,
         "low": close - 1, "close": close, "volume": 1000}
        for i, close in enumerate(closes)
    ]

    rsi = indicators(bars)["rsi"]
    assert rsi[:14] == [None] * 14
    assert rsi[14] == 100.0
    # Fourteen initial +1 changes seed avg gain=1, avg loss=0.
    # One -1 change updates Wilder averages to 13/14 and 1/14.
    assert abs(rsi[15] - (100.0 - 100.0 / (1.0 + 13.0))) < 1e-9


def test_pivots_skip_rsi_warmup_none_values():
    from app.scanner.weekly_swing_scanner import pivots

    values = [None, None, 40, 35, 38, 32, 36, 30, 34]
    assert pivots(values, "low") == [3, 5, 7]
    assert pivots(values, "high") == [4, 6]



def test_nifty200_static_universe_has_200_unique_symbols(monkeypatch):
    from app.scanner import weekly_swing_scanner as scanner

    monkeypatch.setattr(scanner, "NIFTY200_SYMBOLS", tuple(f"SYM{i}" for i in range(1, 201)))
    symbols = scanner._nifty200_symbols()
    assert len(symbols) == 200
    assert symbols[0] == "SYM1"
    assert symbols[-1] == "SYM200"


def test_single_symbol_scan_is_blocked_during_market_hours(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app.scanner import weekly_swing_scanner as scanner

    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: True)
    with pytest.raises(HTTPException) as exc:
        scanner.scan("WIPRO", "NSE")
    assert exc.value.status_code == 409
    assert "off-market only" in exc.value.detail

def test_single_symbol_scan_rechecks_market_hours_between_history_chunks(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app.scanner import weekly_swing_scanner as scanner
    from app.market_data import instruments, client, historical

    # Initial guard passes, first chunk guard passes, second chunk guard blocks.
    market_states = iter([False, False, True])
    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: next(market_states))

    class FakeInstrumentMaster:
        def get_instrument(self, *args, **kwargs):
            return {"token": "123"}

    class FakeMarketDataClient:
        pass

    class FakeHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            self.calls = 0
            FakeHistoricalDataClient.instance = self

        def get_candles(self, **kwargs):
            self.calls += 1
            return {"status": True, "data": []}

    monkeypatch.setattr(instruments, "InstrumentMaster", FakeInstrumentMaster)
    monkeypatch.setattr(client, "MarketDataClient", FakeMarketDataClient)
    monkeypatch.setattr(historical, "HistoricalDataClient", FakeHistoricalDataClient)

    with pytest.raises(HTTPException) as exc:
        scanner.scan("WIPRO", "NSE")

    assert exc.value.status_code == 409
    assert "market hours have started" in exc.value.detail
    assert FakeHistoricalDataClient.instance.calls == 1


def test_single_symbol_scan_rejects_non_nse_exchange(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app.scanner import weekly_swing_scanner as scanner

    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: False)
    with pytest.raises(HTTPException) as exc:
        scanner.scan("WIPRO", "BSE")
    assert exc.value.status_code == 422



def test_history_chunks_are_cached_before_later_market_hours_stop(monkeypatch, tmp_path):
    import pytest
    from fastapi import HTTPException
    from app.scanner import weekly_swing_scanner as scanner
    from app.market_data import instruments, client, historical

    monkeypatch.setattr(scanner, "_cache_root", lambda: tmp_path)
    market_states = iter([False, False, True])

    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: next(market_states))

    class FakeInstrumentMaster:
        def get_instrument(self, *args, **kwargs):
            return {"token": "123"}

    class FakeMarketDataClient:
        pass

    class FakeHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            self.calls = 0

        def get_candles(self, **kwargs):
            self.calls += 1
            return {"status": True, "data": [[f"2021-01-0{self.calls} 09:15", "1", "2", "0.5", "1", "10"]]}

    monkeypatch.setattr(instruments, "InstrumentMaster", FakeInstrumentMaster)
    monkeypatch.setattr(client, "MarketDataClient", FakeMarketDataClient)
    monkeypatch.setattr(historical, "HistoricalDataClient", FakeHistoricalDataClient)
    monkeypatch.setattr(scanner, "HISTORICAL_REQUEST_PAUSE_SECONDS", 0)
    monkeypatch.setattr(scanner, "analyze", lambda ticker, bars: {"status": "ok", "matches": []})
    monkeypatch.setattr(scanner, "weekly_bars", lambda rows: [])

    with pytest.raises(HTTPException) as exc:
        scanner.scan("WIPRO", "NSE")

    assert exc.value.status_code == 409
    rows, last_day = scanner._load_cached_rows("WIPRO")
    assert len(rows) == 1
    assert last_day.isoformat() == "2021-01-01"


def test_nifty200_scan_pauses_entire_run_when_market_opens(monkeypatch):
    from fastapi import HTTPException
    from app.scanner import weekly_swing_scanner as scanner
    from app.market_data import instruments, client, historical

    calls = []
    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: False)
    monkeypatch.setattr(scanner, "_nifty200_symbols", lambda: ["AAA", "BBB"])

    class FakeInstrumentMaster:
        def __init__(self):
            pass

    class FakeMarketDataClient:
        pass

    class FakeHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            pass

    def fake_scan_one(ticker, *args):
        calls.append(ticker)
        raise HTTPException(status_code=409, detail="NSE market hours have started")

    monkeypatch.setattr(instruments, "InstrumentMaster", FakeInstrumentMaster)
    monkeypatch.setattr(client, "MarketDataClient", FakeMarketDataClient)
    monkeypatch.setattr(historical, "HistoricalDataClient", FakeHistoricalDataClient)
    monkeypatch.setattr(scanner, "_scan_one_symbol", fake_scan_one)

    scanner._run_nifty200_scan()

    assert calls == ["AAA"]
    assert scanner._SCAN_STATE["status"] == "paused"
    assert scanner._SCAN_STATE["scanned"] == 1
    assert scanner._SCAN_STATE["fatal_error"] == "NSE market hours have started"

def test_bullish_divergence_requires_indicator_higher_lows_at_price_pivots():
    from app.scanner.weekly_swing_scanner import _has_bullish_divergence

    # Price makes lower lows elsewhere; this predicate checks indicator values
    # at those exact three price-pivot indices.
    assert _has_bullish_divergence([-0.4, -0.2, 0.1], 0, 1, 2)
    assert _has_bullish_divergence([20, 20, 25], 0, 1, 2)
    assert not _has_bullish_divergence([20, 25, 24], 0, 1, 2)
    assert not _has_bullish_divergence([20, 18, 24], 0, 1, 2)
    assert not _has_bullish_divergence([None, 20, 25], 0, 1, 2)
    assert not _has_bullish_divergence([20, float("nan"), 25], 0, 1, 2)



def _weekly_swing_fixture():
    bars = [
        {"week": f"2026-W{i:02d}", "open": 100.0, "high": 110.0, "low": 90.0, "close": 100.0, "volume": 1000.0}
        for i in range(30)
    ]
    # Three descending price swing lows with two intervening swing highs.
    for idx, low in ((3, 80.0), (8, 75.0), (13, 70.0)):
        bars[idx].update(low=low, close=low + 4.0, high=low + 12.0)
    for idx in (5, 10):
        bars[idx].update(high=105.0, low=95.0, close=100.0)
    return bars


def _patch_valid_swing_indicators(monkeypatch, *, confirm=True):
    from app.scanner import weekly_swing_scanner as scanner

    monkeypatch.setattr(scanner, "pivots", lambda values, kind: [3, 8, 13] if kind == "low" else [5, 10])
    series = {key: [0.0] * 30 for key in ("cmf", "obv", "rsi", "mid", "lower", "upper")}
    for idx in range(30):
        series["mid"][idx] = 105.0
        series["lower"][idx] = 60.0
        series["upper"][idx] = 130.0
    # The third price low is one point from the lower band and both highs sit
    # at the middle band. Indicator values align with the exact price pivots.
    series["lower"][13] = 69.0
    series["cmf"][3], series["cmf"][8], series["cmf"][13] = (0.0, 0.1, 0.2) if confirm else (0.2, 0.1, 0.0)
    series["obv"][3], series["obv"][8], series["obv"][13] = (10.0, 11.0, 12.0) if confirm else (10.0, 9.0, 8.0)
    series["rsi"][3], series["rsi"][8], series["rsi"][13] = (30.0, 31.0, 32.0) if confirm else (30.0, 29.0, 28.0)
    monkeypatch.setattr(scanner, "indicators", lambda bars: series)


def test_analyze_qualifies_only_when_pattern_and_indicator_confirmation_align(monkeypatch):
    from app.scanner import weekly_swing_scanner as scanner

    _patch_valid_swing_indicators(monkeypatch, confirm=True)
    result = scanner.analyze("TEST", _weekly_swing_fixture())

    assert result["status"] == "success"
    assert result["count"] == 1
    match = result["matches"][0]
    assert [point["low"] for point in match["lows"]] == [80.0, 75.0, 70.0]
    assert [point["high"] for point in match["intervening_highs"]] == [105.0, 105.0]
    assert match["indicators_confirmed"] == ["CMF", "OBV", "RSI"]
    assert match["third_low_lower_band"] == 69.0
    assert result["orders_placed"] is False



def test_analyze_strength_matches_exact_number_of_confirming_indicators(monkeypatch):
    import pytest
    from app.scanner import weekly_swing_scanner as scanner

    bars = _weekly_swing_fixture()
    expected = [
        (("CMF",), "QUALIFIED"),
        (("CMF", "OBV"), "STRONG"),
        (("CMF", "OBV", "RSI"), "STRONGEST"),
    ]
    for enabled, strength in expected:
        _patch_valid_swing_indicators(monkeypatch, confirm=False)
        series = scanner.indicators(bars)
        for name in enabled:
            key = name.lower()
            series[key][3], series[key][8], series[key][13] = (10.0, 11.0, 12.0)
        monkeypatch.setattr(scanner, "indicators", lambda bars, series=series: series)

        result = scanner.analyze("TEST", bars)
        assert result["count"] == 1
        assert result["matches"][0]["strength"] == strength
        assert result["matches"][0]["indicators_confirmed"] == list(enabled)

def test_analyze_rejects_pattern_without_any_indicator_confirmation(monkeypatch):
    from app.scanner import weekly_swing_scanner as scanner

    _patch_valid_swing_indicators(monkeypatch, confirm=False)
    result = scanner.analyze("TEST", _weekly_swing_fixture())

    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["matches"] == []


def test_analyze_rejects_third_low_far_from_lower_bollinger_band(monkeypatch):
    from app.scanner import weekly_swing_scanner as scanner

    _patch_valid_swing_indicators(monkeypatch, confirm=True)
    series = scanner.indicators(_weekly_swing_fixture())
    series["lower"][13] = 40.0
    monkeypatch.setattr(scanner, "indicators", lambda bars: series)
    result = scanner.analyze("TEST", _weekly_swing_fixture())

    assert result["count"] == 0
    assert result["matches"] == []


def test_analyze_rejects_missing_intervening_swing_high(monkeypatch):
    from app.scanner import weekly_swing_scanner as scanner

    _patch_valid_swing_indicators(monkeypatch, confirm=True)
    monkeypatch.setattr(scanner, "pivots", lambda values, kind: [3, 8, 13] if kind == "low" else [5])
    result = scanner.analyze("TEST", _weekly_swing_fixture())

    assert result["count"] == 0
    assert result["matches"] == []


def test_cache_loader_sorts_deduplicates_and_rejects_malformed_rows(monkeypatch, tmp_path):
    import json
    from app.scanner import weekly_swing_scanner as scanner

    monkeypatch.setattr(scanner, "_cache_root", lambda: tmp_path)
    payload = {
        "schema_version": scanner.CACHE_SCHEMA_VERSION,
        "symbol": "TEST",
        "rows": [
            ["2026-01-07 09:15", "102", "106", "101", "105", "1200"],
            ["not-a-date", "100", "105", "98", "102", "1000"],
            ["2026-01-05 09:15", "100", "105", "98", "102", "1000"],
            ["2026-01-07 09:15", "103", "107", "102", "106", "1300"],
            ["2026-01-08 09:15", "nan", "107", "102", "106", "1300"],
        ],
    }
    (tmp_path / "TEST.json").write_text(json.dumps(payload), encoding="utf-8")

    rows, last_day = scanner._load_cached_rows("TEST")

    assert [row[0][:10] for row in rows] == ["2026-01-05", "2026-01-07"]
    assert rows[-1][4] == "106"
    assert last_day.isoformat() == "2026-01-07"


def test_cached_history_fetches_only_dates_after_latest_cached_candle(monkeypatch, tmp_path):
    from datetime import date, timedelta
    from app.scanner import weekly_swing_scanner as scanner
    from app.market_data import instruments, client, historical

    monkeypatch.setattr(scanner, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: False)
    monkeypatch.setattr(scanner, "HISTORICAL_REQUEST_PAUSE_SECONDS", 0)
    end = date(2026, 10, 23)
    old_day = end - timedelta(days=1)
    scanner._save_cached_rows("TEST", [[f"{old_day.isoformat()} 09:15", "100", "105", "98", "102", "1000"]])

    class FakeInstrumentMaster:
        def get_instrument(self, *args, **kwargs):
            return {"token": "123"}

    class FakeMarketDataClient:
        pass

    class FakeHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            self.requests = []

        def get_candles(self, **kwargs):
            self.requests.append(kwargs)
            return {"status": True, "data": [[f"{end.isoformat()} 09:15", "102", "106", "101", "105", "1200"]]}

    monkeypatch.setattr(instruments, "InstrumentMaster", FakeInstrumentMaster)
    monkeypatch.setattr(client, "MarketDataClient", FakeMarketDataClient)
    monkeypatch.setattr(historical, "HistoricalDataClient", FakeHistoricalDataClient)
    monkeypatch.setattr(scanner, "weekly_bars", lambda rows: [{"week": "2026-10-19"}] * 25)
    monkeypatch.setattr(scanner, "analyze", lambda ticker, bars: {"status": "success", "matches": []})

    history = FakeHistoricalDataClient()
    result = scanner._scan_one_symbol("TEST", FakeInstrumentMaster(), history, end)

    assert len(history.requests) == 1
    assert history.requests[0]["from_date"] == f"{end.isoformat()} 09:15"
    assert result["history_cache"] == "updated"
    rows, last_day = scanner._load_cached_rows("TEST")
    assert len(rows) == 2
    assert last_day == end


def test_cached_history_is_reused_without_historical_api_call(monkeypatch, tmp_path):
    from datetime import date
    from app.scanner import weekly_swing_scanner as scanner

    monkeypatch.setattr(scanner, "_cache_root", lambda: tmp_path)
    day = date(2026, 10, 23)
    scanner._save_cached_rows("TEST", [[f"{day.isoformat()} 09:15", "100", "105", "98", "102", "1000"]])

    class FakeInstrumentMaster:
        def get_instrument(self, *args, **kwargs):
            return {"token": "123"}

    class NeverCallHistorical:
        def get_candles(self, **kwargs):
            raise AssertionError("Cached history should not trigger a historical fetch")

    monkeypatch.setattr(scanner, "weekly_bars", lambda rows: [{"week": "2026-10-19"}] * 25)
    monkeypatch.setattr(scanner, "analyze", lambda ticker, bars: {"status": "success", "matches": []})
    result = scanner._scan_one_symbol("TEST", FakeInstrumentMaster(), NeverCallHistorical(), day)

    assert result["history_cache"] == "reused"
    assert result["history_last_day"] == day.isoformat()


def test_nse_today_uses_exchange_timezone(monkeypatch):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app.scanner import weekly_swing_scanner as scanner

    # UTC is still on Oct 23, while the NSE-local date has advanced to Oct 24.
    fixed_utc = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)
    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc.replace(tzinfo=None)

    monkeypatch.setattr(scanner, "datetime", FixedDateTime)
    assert scanner._nse_today().isoformat() == "2026-10-24"



def test_history_fetch_ranges_repairs_large_interior_gap_and_tail():
    from datetime import date
    from app.scanner.weekly_swing_scanner import _history_fetch_ranges

    rows = [
        ["2026-01-05 09:15", "100", "105", "98", "102", "1000"],
        ["2026-01-20 09:15", "102", "106", "101", "105", "1200"],
    ]
    assert _history_fetch_ranges(rows, date(2026, 1, 23)) == [
        (date(2026, 1, 6), date(2026, 1, 19)),
        (date(2026, 1, 21), date(2026, 1, 23)),
    ]


def test_history_fetch_ranges_does_not_redownload_small_closure():
    from datetime import date
    from app.scanner.weekly_swing_scanner import _history_fetch_ranges

    rows = [
        ["2026-01-05 09:15", "100", "105", "98", "102", "1000"],
        ["2026-01-08 09:15", "102", "106", "101", "105", "1200"],
    ]
    assert _history_fetch_ranges(rows, date(2026, 1, 8)) == []



def test_nse_trading_day_excludes_published_2026_holidays_and_weekends():
    from datetime import date
    from app.scanner.weekly_swing_scanner import _is_nse_trading_day

    assert not _is_nse_trading_day(date(2026, 1, 15))  # Maharashtra municipal corporation election
    assert not _is_nse_trading_day(date(2026, 10, 20))  # Dussehra
    assert not _is_nse_trading_day(date(2026, 11, 10))  # Diwali-Balipratipada
    assert not _is_nse_trading_day(date(2026, 10, 24))  # Saturday
    assert _is_nse_trading_day(date(2026, 10, 26))  # ordinary Monday


def test_nse_trading_day_uses_weekday_fallback_for_future_years():
    from datetime import date
    from app.scanner.weekly_swing_scanner import _is_nse_trading_day

    # Future-year holidays need a refreshed NSE calendar, but weekdays must not
    # permanently disable the daily scanner when the year changes.
    assert _is_nse_trading_day(date(2027, 1, 4))  # Monday
    assert not _is_nse_trading_day(date(2027, 1, 2))  # Saturday



def test_market_hours_gate_respects_known_nse_holiday(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.scanner import weekly_swing_scanner as scanner

    ist = ZoneInfo("Asia/Kolkata")

    class FixedDateTime:
        current = datetime(2026, 10, 20, 10, 0, tzinfo=ist)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(scanner, "datetime", FixedDateTime)
    assert scanner._nse_market_is_open_now() is False

    FixedDateTime.current = datetime(2026, 10, 21, 10, 0, tzinfo=ist)
    assert scanner._nse_market_is_open_now() is True


def test_history_fetch_ranges_with_existing_cache_fetches_tail_without_full_lookback():
    from datetime import date
    from app.scanner.weekly_swing_scanner import _history_fetch_ranges

    end = date(2026, 10, 23)
    rows = [
        ["2024-01-05 09:15", "100", "105", "98", "102", "1000"],
    ]
    # Existing cache is incrementally extended; five-year backfill is only
    # triggered when there are no cached candles at all.
    assert _history_fetch_ranges(rows, end) == [
        (date(2024, 1, 6), end),
    ]


def test_future_dated_cache_rows_are_excluded_and_current_tail_is_fetched(monkeypatch, tmp_path):
    from datetime import date, timedelta
    from app.scanner import weekly_swing_scanner as scanner
    from app.market_data import instruments, client, historical

    monkeypatch.setattr(scanner, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(scanner, "_nse_market_is_open_now", lambda: False)
    monkeypatch.setattr(scanner, "HISTORICAL_REQUEST_PAUSE_SECONDS", 0)
    end = date(2026, 10, 23)
    previous = end - timedelta(days=1)
    future = end + timedelta(days=10)
    scanner._save_cached_rows("TEST", [
        [f"{previous.isoformat()} 09:15", "100", "105", "98", "102", "1000"],
        [f"{future.isoformat()} 09:15", "102", "106", "101", "105", "1200"],
    ])

    class FakeInstrumentMaster:
        def get_instrument(self, *args, **kwargs):
            return {"token": "123"}

    class FakeMarketDataClient:
        pass

    class FakeHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            self.requests = []

        def get_candles(self, **kwargs):
            self.requests.append(kwargs)
            return {"status": True, "data": [[f"{end.isoformat()} 09:15", "102", "106", "101", "105", "1200"]]}

    monkeypatch.setattr(instruments, "InstrumentMaster", FakeInstrumentMaster)
    monkeypatch.setattr(client, "MarketDataClient", FakeMarketDataClient)
    monkeypatch.setattr(historical, "HistoricalDataClient", FakeHistoricalDataClient)
    monkeypatch.setattr(scanner, "weekly_bars", lambda rows: [{"week": "2026-10-19"}] * 25)
    monkeypatch.setattr(scanner, "analyze", lambda ticker, bars: {"status": "success", "matches": []})

    history = FakeHistoricalDataClient()
    result = scanner._scan_one_symbol("TEST", FakeInstrumentMaster(), history, end)

    assert len(history.requests) == 1
    assert history.requests[0]["from_date"] == f"{end.isoformat()} 09:15"
    assert result["history_last_day"] == end.isoformat()
    rows, last_day = scanner._load_cached_rows("TEST")
    assert all(date.fromisoformat(row[0][:10]) <= end for row in rows)
    assert last_day == end