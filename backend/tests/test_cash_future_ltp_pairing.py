import time

from app.core.config import settings
from app.scanner.live_cash_future_scanner import LiveCashFutureScanner


def _pair(scanner, *, cash_book=True, future_book=True, cash_qty=1, future_qty=1):
    timestamp_ns = time.time_ns()
    cash = {
        "leg": "CASH",
        "underlying": "ABC",
        "ltp": 100.0,
        "source_timestamp_ns": timestamp_ns,
        "received_at_ns": time.time_ns(),
        "volume": 0,
    }
    future = {
        "leg": "FUTURE",
        "underlying": "ABC",
        "contract_month": "CURRENT",
        "ltp": 101.5,
        "source_timestamp_ns": timestamp_ns,
        "received_at_ns": time.time_ns(),
        "volume": 0,
    }
    if cash_book:
        cash.update({"bid": 99.9, "ask": 100.1, "bid_qty": cash_qty, "ask_qty": cash_qty})
    if future_book:
        future.update({"bid": 101.4, "ask": 101.6, "bid_qty": future_qty, "ask_qty": future_qty})
    scanner.observe(cash)
    return scanner.observe(future)


def test_ltp_pair_is_returned_when_bid_ask_are_missing(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_CASH_FUTURE_MAX_QUOTE_AGE_SECONDS", 5.0)
    scanner = LiveCashFutureScanner()

    signal = _pair(scanner, cash_book=False, future_book=False)

    assert signal is not None
    assert signal.cash_ltp == 100.0
    assert signal.future_ltp == 101.5
    assert signal.gap == 1.5
    assert signal.cash_ask is None
    assert signal.future_bid is None
    assert signal.eligible is False
    rows = scanner.snapshot(max_age_seconds=5.0, limit=10)
    assert len(rows) == 1
    assert rows[0]["lifecycle"] == "PAIR_ONLY"
    assert "EXECUTION_QUOTES_INCOMPLETE_INFORMATIONAL" in rows[0]["reason_codes"]
    health = scanner.health()
    assert health["pairs"] == 1
    assert health["pairs_missing_quotes"] == 1
    assert health["pairs_with_quotes"] == 0
    assert health["last_observation_at"] is not None


def test_low_liquidity_and_zero_volume_do_not_block_pair(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_CASH_FUTURE_MAX_QUOTE_AGE_SECONDS", 5.0)
    monkeypatch.setattr(settings, "LIVE_CASH_FUTURE_MIN_LIQUIDITY_QTY", 1_000_000)
    scanner = LiveCashFutureScanner()

    signal = _pair(scanner, cash_qty=1, future_qty=1)

    assert signal is not None
    assert signal.cash_ltp == 100.0
    assert signal.future_ltp == 101.5
    assert signal.liquidity_qty == 1
    assert scanner.health()["pairs_dropped_liquidity"] == 0
    assert len(scanner.snapshot(max_age_seconds=5.0, limit=10)) == 1


def test_bid_ask_are_read_only_and_never_change_pair_gap_or_emit_alert(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_CASH_FUTURE_MAX_QUOTE_AGE_SECONDS", 5.0)
    scanner = LiveCashFutureScanner()

    signal = _pair(scanner, cash_book=True, future_book=True)

    assert signal is not None
    # LTP gap is authoritative even when a book is present.
    assert signal.gap == signal.future_ltp - signal.cash_ltp == 1.5
    assert signal.cash_ask == 100.1
    assert signal.future_bid == 101.4
    assert signal.eligible is False
    assert signal.alert_eligible is False
    assert signal.alert_event is None
    assert signal.lifecycle == "PAIR_ONLY"
