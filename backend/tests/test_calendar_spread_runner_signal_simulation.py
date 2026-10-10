"""Deterministic runner-to-signal simulation; no broker/WebSocket or live orders."""
import time

from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.live_calendar_spread_stream import LiveCalendarSpreadOneSecondCollector
from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner


def _record(token, symbol, expiry, timestamp_ns, bid, ask, *, underlying="NIFTY"):
    return MarketDataRecord(
        instrument=InstrumentKey("NFO", "NFO", token),
        symbol=symbol,
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=timestamp_ns,
        timeframe="1s",
        ltp=(bid + ask) / 2,
        bid=bid,
        ask=ask,
        bid_qty=100,
        ask_qty=100,
        underlying=underlying,
        expiry=expiry,
        lot_size=50,
        tick_size=0.05,
    )


def test_calendar_runner_feed_bucket_to_executable_signal(monkeypatch):
    scanner = LiveCalendarSpreadScanner(minimum_gap_points=3, minimum_gross_profit=150)
    collector = LiveCalendarSpreadOneSecondCollector(
        data_db=":memory:",
        on_observation=scanner.observe,
    )
    # This is a feed simulation, so bypass only the exchange-session clock gate.
    monkeypatch.setattr(collector, "_exchange_open", lambda exchange, value: True)
    collector._kind_by_key = {
        ("NFO", "near-token"): "INDEX_FUTURE",
        ("NFO", "far-token"): "INDEX_FUTURE",
    }

    # Use recent timestamps across two 1-second buckets; no sleep or broker login.
    base = time.time_ns() - 2_500_000_000
    records = [
        _record("near-token", "NIFTY-NEAR", "2026-10-29", base + 100_000_000, 99, 100),
        _record("far-token", "NIFTY-FAR", "2026-11-26", base + 200_000_000, 104, 105),
        _record("near-token", "NIFTY-NEAR", "2026-10-29", base + 1_100_000_000, 99, 100),
        _record("far-token", "NIFTY-FAR", "2026-11-26", base + 1_200_000_000, 104, 105),
    ]
    for record in records:
        collector._observe_record(record)

    # Mirror _run_session's orderly final bucket flush.
    with collector._lock:
        pending = list(collector._latest.values())
        collector._latest.clear()
    for bucket_timestamp, record in pending:
        collector._emit(record, bucket_timestamp)

    signals = scanner.snapshot()
    assert collector.snapshot()["diagnostics"]["records_received"] == 4
    assert collector.snapshot()["diagnostics"]["records_emitted"] == 4
    assert collector.snapshot()["diagnostics"]["callback_errors"] == 0
    assert scanner.diagnostics_snapshot()["counters"]["observe_errors"] == 0
    assert len(signals) == 1
    signal = signals[0]
    assert signal.underlying == "NIFTY"
    assert signal.contract_family == "INDEX_FUTURE"
    assert signal.near_contract_month == "2026-10-29"
    assert signal.far_contract_month == "2026-11-26"
    assert signal.direction == "LONG_NEAR_SHORT_FAR"
    assert signal.gap_points == 4
    assert signal.gross_profit == 200
    assert signal.liquidity_qty == 100
    assert signal.qualifies is True
    assert signal.signal.metadata["live_orders"] is False
    assert signal.signal.metadata["source"] == "common-market-data"


def test_calendar_signal_snapshot_keeps_allowed_future_timestamp():
    scanner = LiveCalendarSpreadScanner(minimum_gap_points=3, minimum_gross_profit=150)
    future_ns = time.time_ns() + 500_000_000
    near = _record("near-token", "NIFTY-NEAR", "2026-10-29", future_ns, 99, 100)
    far = _record("far-token", "NIFTY-FAR", "2026-11-26", future_ns, 104, 105)

    assert scanner.update(near) is None
    signal = scanner.update(far)

    assert signal is not None
    assert len(scanner.snapshot()) == 1
    assert len(scanner.pair_snapshot()) == 1
