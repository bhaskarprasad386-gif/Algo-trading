from app.scanner.live_cash_future_scanner import LiveCashFutureScanner


def test_live_scanner_pairs_same_second_cash_and_future():
    scanner = LiveCashFutureScanner()
    cash = {
        "leg": "CASH", "underlying": "ABC", "contract_month": None,
        "ltp": 100.0, "bid": 99.9, "ask": 100.0, "source_timestamp_ns": 1_000_000_000,
    }
    future = {
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 100.8, "ask": 101.0, "source_timestamp_ns": 1_000_000_000,
    }
    assert scanner.observe(cash) is None
    signal = scanner.observe(future)
    assert signal is not None
    assert signal.gap == 0.8
    assert round(signal.gap_pct, 3) == 0.8


def test_live_scanner_does_not_pair_different_seconds():
    scanner = LiveCashFutureScanner()
    assert scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100.0,
        "bid": 99.9, "ask": 100.0, "source_timestamp_ns": 1_000_000_000,
    }) is None
    assert scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 100.8, "ask": 101.0, "source_timestamp_ns": 2_000_000_000,
    }) is None


def test_live_scanner_requires_executable_two_sided_quotes():
    scanner = LiveCashFutureScanner()
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100.0,
        "bid": 99.9, "source_timestamp_ns": 1_000_000_000,
    })
    assert scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 100.8, "ask": 101.0, "source_timestamp_ns": 1_000_000_000,
    }) is None


def test_live_scanner_advanced_metrics(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_MIN_STABLE_OBSERVATIONS", 1)
    scanner = LiveCashFutureScanner()
    ts = 1_000_000_000
    scanner.observe({"leg":"CASH","underlying":"ABC","ltp":100,"bid":99.9,"ask":100,"source_timestamp_ns":ts})
    signal = scanner.observe({"leg":"FUTURE","underlying":"ABC","contract_month":"CURRENT",
                              "ltp":101,"bid":100.8,"ask":101,"lot_size":100,
                              "expiry":"30SEP2026","source_timestamp_ns":ts})
    assert signal is not None
    assert signal.gross_lot_value == 80.0
    assert signal.net_gap == signal.gap
    assert signal.cash_day_high == 100
    assert signal.cash_day_low == 100
    assert signal.future_day_high == 101
    assert signal.future_day_low == 101
    assert signal.stable_observations == 1
    assert signal.annualized_gap_pct is not None
