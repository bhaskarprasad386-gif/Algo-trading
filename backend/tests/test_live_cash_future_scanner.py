from time import time

import pytest

from app.scanner.live_cash_future_scanner import LiveCashFutureScanner


def test_live_scanner_pairs_same_second_cash_and_future():
    scanner = LiveCashFutureScanner()
    cash = {
        "leg": "CASH", "underlying": "ABC", "contract_month": None,
        "ltp": 100.0, "bid": 99.9, "ask": 100.0, "source_timestamp_ns": 1_000_000_000,
    }
    future = {
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 100.8, "ask": 101.0, "bid_qty": 800, "ask_qty": 700, "source_timestamp_ns": 1_000_000_000,
    }
    assert scanner.observe(cash) is None
    signal = scanner.observe(future)
    assert signal is not None
    assert signal.gap == pytest.approx(0.8)
    assert round(signal.gap_pct, 3) == 0.8


def test_live_scanner_rejects_pair_beyond_one_second():
    scanner = LiveCashFutureScanner()
    assert scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100.0,
        "bid": 99.9, "ask": 100.0, "bid_qty": 1000, "ask_qty": 900, "source_timestamp_ns": 1_000_000_000,
    }) is None
    assert scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 100.8, "ask": 101.0, "source_timestamp_ns": 3_000_000_000,
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
    assert signal.gross_lot_value == pytest.approx(80.0)
    assert signal.net_gap < signal.gap
    assert signal.cash_day_high == 100
    assert signal.cash_day_low == 100
    assert signal.future_day_high == 101
    assert signal.future_day_low == 101
    assert signal.stable_observations == 1
    assert signal.annualized_gap_pct is not None


def test_live_scanner_liquidity_capacity_and_traceability(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_MIN_LIQUIDITY_QTY", 500)
    scanner = LiveCashFutureScanner()
    ts = 1_000_000_000
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
        "bid_qty": 1000, "ask_qty": 900, "source_timestamp_ns": ts,
    })
    signal = scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.8, "ask": 101, "bid_qty": 800, "ask_qty": 700,
        "lot_size": 100, "expiry": "30SEP2026", "source_timestamp_ns": ts,
    })
    assert signal is not None
    assert signal.liquidity_qty == 700
    assert signal.capacity_lots == 1000
    assert signal.capacity_notional == 10_000_000
    assert signal.observation_ref == "ABC:CURRENT:1000000000"
    assert "LIQUIDITY_MEASURED" in signal.reason_codes


def test_live_scanner_lifecycle_recovery_and_current_near_comparison(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_MIN_STABLE_OBSERVATIONS", 1)
    scanner = LiveCashFutureScanner()
    base = int(time() * 1_000_000_000)

    def pair(month, ts, bid):
        scanner.observe({
            "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
            "source_timestamp_ns": ts,
        })
        return scanner.observe({
            "leg": "FUTURE", "underlying": "ABC", "contract_month": month,
            "ltp": bid + 0.2, "bid": bid, "ask": bid + 0.2, "lot_size": 1,
            "source_timestamp_ns": ts,
        })

    first = pair("CURRENT", base, 101)
    assert first.lifecycle == "NEW"
    assert first.alert_event == "NEW"
    second = pair("CURRENT", base + 1_000_000_000, 101)
    assert second.lifecycle == "ACTIVE"
    assert second.alert_event is None
    # Compare current/near while both contracts are still actionable. Expired
    # signals are intentionally excluded from the live-fast snapshot.
    near = pair("NEAR", base + 2_000_000_000, 100.2)
    assert near is not None
    rows = scanner.snapshot(max_age_seconds=10_000, limit=10)
    current = next(row for row in rows if row["contract_month"] == "CURRENT")
    assert current["peer_contract_month"] == "NEAR"
    assert current["gap_pct_delta_vs_peer"] is not None
    assert current["is_best_contract_month"] is True

    weakening = pair("CURRENT", base + 3_000_000_000, 100.5)
    assert weakening.lifecycle == "WEAKENING"
    expired = pair("CURRENT", base + 4_000_000_000, 99.5)
    assert expired.lifecycle == "EXPIRED"
    assert expired.alert_event is None


def test_live_scanner_ranking_exposes_multi_factor_score():
    scanner = LiveCashFutureScanner()
    ts = int(time() * 1_000_000_000)
    for month, bid in (("CURRENT", 102.0), ("NEAR", 101.0)):
        scanner.observe({
            "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
            "source_timestamp_ns": ts,
        })
        scanner.observe({
            "leg": "FUTURE", "underlying": "ABC", "contract_month": month,
            "ltp": bid, "bid": bid, "ask": bid + 0.1, "lot_size": 1, "source_timestamp_ns": ts,
        })
    rows = scanner.snapshot(max_age_seconds=10_000, limit=10)
    assert rows
    assert all(0.0 <= row["rank_score"] <= 1.0 for row in rows)
    assert "rank_factors" in rows[0]


def test_live_scanner_emits_recovery_event_after_expiry(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_MIN_STABLE_OBSERVATIONS", 1)
    scanner = LiveCashFutureScanner()
    base = int(time() * 1_000_000_000)

    def pair(ts, bid):
        scanner.observe({
            "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
            "source_timestamp_ns": ts,
        })
        return scanner.observe({
            "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
            "ltp": bid, "bid": bid, "ask": bid + 0.1, "lot_size": 1, "source_timestamp_ns": ts,
        })

    assert pair(base, 101).alert_event == "NEW"
    assert pair(base + 1_000_000_000, 99).alert_event is None
    assert pair(base + 2_000_000_000, 101).alert_event == "RECOVERY"


def test_live_scanner_liquidity_filter_requires_all_four_depth_sides(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_MIN_LIQUIDITY_QTY", 100)
    scanner = LiveCashFutureScanner()
    ts = 1_000_000_000
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
        "bid_qty": 200, "ask_qty": 200, "source_timestamp_ns": ts,
    })
    assert scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.8, "ask": 101,
        "bid_qty": 200, "source_timestamp_ns": ts,
    }) is None


def test_live_scanner_custom_gross_profit_filter_controls_alert(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_ALERT_MIN_GROSS_PROFIT", 100.0)
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_ALERT_LOTS", 2)
    scanner = LiveCashFutureScanner()
    ts = 1_000_000_000
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": ts,
    })
    below = scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.4, "ask": 101, "lot_size": 100,
        "expiry": "30SEP2026", "source_timestamp_ns": ts,
    })
    assert below is not None
    assert below.gross_profit == pytest.approx(80.0)
    assert below.alert_event is None

    ts += 1_000_000_000
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": ts,
    })
    above = scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 102, "bid": 101, "ask": 102, "lot_size": 100,
        "expiry": "30SEP2026", "source_timestamp_ns": ts,
    })
    assert above is not None
    assert above.gross_profit == 200.0
    assert above.alert_lots == 2
    assert above.alert_event == "RECOVERY"


def test_live_scanner_does_not_alert_when_capacity_is_zero(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_CAPITAL", 50.0)
    scanner = LiveCashFutureScanner()
    ts = 1_000_000_000
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": ts,
    })
    signal = scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.8, "ask": 101, "lot_size": 100,
        "expiry": "30SEP2026", "source_timestamp_ns": ts,
    })
    assert signal is not None
    assert signal.capacity_lots == 0
    assert signal.alert_lots == 0
    assert signal.alert_event is None


def test_live_scanner_keeps_only_active_day_extremes():
    scanner = LiveCashFutureScanner()
    ts = 1_000_000_000
    scanner.observe({"leg":"CASH","underlying":"OLD","ltp":100,"ask":100,"source_timestamp_ns":ts})
    scanner.observe({"leg":"FUTURE","underlying":"OLD","contract_month":"CURRENT","ltp":101,"bid":100.8,"ask":101,"source_timestamp_ns":ts})
    old_key = next(iter(scanner._session_extremes))
    scanner.observe({"leg":"CASH","underlying":"NEW","ltp":200,"ask":200,"source_timestamp_ns":ts + 86_400_000_000_000})
    assert old_key not in scanner._session_extremes

def test_live_scanner_pairs_within_one_second_and_uses_wall_clock_age():
    scanner = LiveCashFutureScanner()
    now = time() * 1_000_000_000
    assert scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": int(now),
    }) is None
    signal = scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.8, "ask": 101, "source_timestamp_ns": int(now + 1_000_000_000),
    })
    assert signal is not None

    stale = scanner.observe({
        "leg": "CASH", "underlying": "STALE", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": int(now), "received_at_ns": int(now - 10_000_000_000),
    })
    assert stale is None


def test_live_scanner_rejects_crossed_market():
    scanner = LiveCashFutureScanner()
    ts = int(time() * 1_000_000_000)
    assert scanner.observe({
        "leg": "CASH", "underlying": "BAD", "ltp": 100, "bid": 101, "ask": 100,
        "source_timestamp_ns": ts,
    }) is None


def test_live_scanner_stability_survives_one_missed_second(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_MIN_STABLE_OBSERVATIONS", 1)
    scanner = LiveCashFutureScanner()
    base = int(time() * 1_000_000_000)
    def pair(ts):
        scanner.observe({"leg":"CASH","underlying":"ABC","ltp":100,"bid":99.9,"ask":100,"source_timestamp_ns":ts})
        return scanner.observe({"leg":"FUTURE","underlying":"ABC","contract_month":"CURRENT","ltp":101,"bid":100.8,"ask":101,"source_timestamp_ns":ts})
    assert pair(base).stable_observations == 1
    assert pair(base + 2_000_000_000).stable_observations == 2


def test_live_scanner_exposes_absolute_quality_score():
    scanner = LiveCashFutureScanner()
    ts = int(time() * 1_000_000_000)
    scanner.observe({"leg":"CASH","underlying":"ABC","ltp":100,"bid":99.9,"ask":100,"bid_qty":1000,"ask_qty":1000,"source_timestamp_ns":ts})
    signal = scanner.observe({"leg":"FUTURE","underlying":"ABC","contract_month":"CURRENT","ltp":102,"bid":101,"ask":102,"bid_qty":1000,"ask_qty":1000,"lot_size":1,"source_timestamp_ns":ts})
    assert signal is not None
    assert 0 <= signal.quality_score <= 100
    row = scanner.snapshot(max_age_seconds=10, limit=1)[0]
    assert row["quality_score"] == signal.quality_score
    assert row["comparison"] == "single contract"


def test_live_scanner_snapshot_excludes_stale_signals():
    scanner = LiveCashFutureScanner()
    now_ns = int(time() * 1_000_000_000)
    scanner.observe({
        "leg": "CASH", "underlying": "FRESH", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": now_ns,
    })
    fresh = scanner.observe({
        "leg": "FUTURE", "underlying": "FRESH", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.8, "ask": 101, "lot_size": 1,
        "source_timestamp_ns": now_ns,
    })
    assert fresh is not None
    scanner.observe({
        "leg": "CASH", "underlying": "STALE", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": now_ns - 10_000_000_000,
        "received_at_ns": now_ns - 10_000_000_000,
    })
    # The live observer correctly drops a quote whose receive time is already
    # older than the configured ingestion-age guard. The snapshot assertion
    # below therefore verifies that the stale symbol cannot appear.
    scanner.observe({
        "leg": "FUTURE", "underlying": "STALE", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.8, "ask": 101, "lot_size": 1,
        "source_timestamp_ns": now_ns - 10_000_000_000,
        "received_at_ns": now_ns - 10_000_000_000,
    })
    rows = scanner.snapshot(max_age_seconds=5.0, limit=50)
    assert [row["symbol"] for row in rows] == ["FRESH"]


def test_live_scanner_snapshot_uses_receive_time_not_exchange_timestamp():
    scanner = LiveCashFutureScanner()
    source_ts = int(time() * 1_000_000_000) - 10_000_000_000
    received_ts = int(time() * 1_000_000_000)
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100, "bid": 99.9, "ask": 100,
        "source_timestamp_ns": source_ts, "received_at_ns": received_ts,
    })
    signal = scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101, "bid": 100.8, "ask": 101,
        "source_timestamp_ns": source_ts, "received_at_ns": received_ts,
    })
    assert signal is not None
    rows = scanner.snapshot(max_age_seconds=5, limit=10)
    assert rows
    assert rows[0]["received_at_ns"] == received_ts



def test_live_scanner_rejects_out_of_order_same_leg_updates_and_counts_reason(monkeypatch):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_PAIR_TOLERANCE_SECONDS", 1.0)
    scanner = LiveCashFutureScanner()
    assert scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100,
        "bid": 99.9, "ask": 100, "source_timestamp_ns": 10_000_000_000,
    }) is None
    assert scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 99,
        "bid": 98.9, "ask": 99, "source_timestamp_ns": 9_500_000_000,
    }) is None
    health = scanner.health()
    assert health["out_of_order_updates"] == 1
    assert health["dropped"] >= 1


def test_live_scanner_cache_is_bounded_and_reports_evictions():
    scanner = LiveCashFutureScanner()
    scanner._max_latest_buckets = 2
    base = int(time() * 1_000_000_000)
    for index in range(5):
        scanner.observe({
            "leg": "CASH", "underlying": f"SYM{index}", "ltp": 100,
            "bid": 99.9, "ask": 100, "source_timestamp_ns": base + index * 1_000_000_000,
        })
    health = scanner.health()
    assert health["latest_cache_buckets"] <= 2
    assert health["latest_cache_limit"] == 2
    assert health["cache_evictions"] >= 3


def test_live_scanner_counts_invalid_and_stale_input_categories():
    scanner = LiveCashFutureScanner()
    assert scanner.observe(None) is None
    assert scanner.observe({"leg": "CASH", "underlying": "ABC", "ltp": 100}) is None
    assert scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100,
        "source_timestamp_ns": int(time() * 1_000_000_000),
        "received_at_ns": int(time() * 1_000_000_000) - 10_000_000_000,
    }) is None
    health = scanner.health()
    assert health["invalid_payloads"] == 1
    assert health["invalid_timestamps"] == 1
    assert health["stale_quotes"] == 1
