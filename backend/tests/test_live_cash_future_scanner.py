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
