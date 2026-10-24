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


def test_pivots_skip_rsi_warmup_none_values():
    from app.scanner.weekly_swing_scanner import pivots

    values = [None, None, 40, 35, 38, 32, 36, 30, 34]
    assert pivots(values, "low") == [3, 5, 7]
    assert pivots(values, "high") == [4, 6]



def test_nifty200_constituent_parser_strips_bom_and_deduplicates(monkeypatch):
    from app.scanner import weekly_swing_scanner as scanner

    rows = ["Symbol,Company Name"]
    rows.extend(f"SYM{i},Company {i}" for i in range(1, 201))
    rows.append("SYM1,Duplicate")

    class Response:
        text = chr(65279) + "\n".join(rows)

        def raise_for_status(self):
            return None

    monkeypatch.setattr(scanner.requests, "get", lambda *args, **kwargs: Response())
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
