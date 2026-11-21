from app.scanner.early_impulse_synchronized_snapshot import TimedQuote, build_synchronized_snapshot


def q(name, ts, price=100):
    return TimedQuote(name, ts, price, bid=price - .1, ask=price + .1, bid_qty=10, ask_qty=12, volume_delta=3)


def test_snapshot_accepts_fresh_quotes_within_skew():
    result = build_synchronized_snapshot(
        {"spot": [q("spot", 1_000_000_000)], "future": [q("future", 1_200_000_000)]},
        now_ns=1_500_000_000, required_instruments=["spot", "future"],
    )
    assert result["aligned"] is True
    assert result["observed_skew_ns"] == 200_000_000
    assert result["scanner_only"] is True
    assert result["order_routing_enabled"] is False


def test_snapshot_fails_closed_on_skew_and_missing_required():
    result = build_synchronized_snapshot(
        {"spot": [q("spot", 1_000_000_000)], "future": [q("future", 1_900_000_000)]},
        now_ns=2_000_000_000, required_instruments=["spot", "future", "confirm"],
    )
    assert result["aligned"] is False
    assert result["observed_skew_ns"] == 900_000_000
    assert result["missing_instruments"] == ["confirm"]


def test_snapshot_rejects_stale_and_future_timestamps():
    result = build_synchronized_snapshot(
        {"stale": [q("stale", 1_000_000_000)], "future": [q("future", 3_000_000_000)]},
        now_ns=2_000_000_000, required_instruments=["stale", "future"],
    )
    assert result["aligned"] is False
    assert result["stale_instruments"] == ["stale"]
    assert result["future_timestamp_instruments"] == ["future"]


def test_snapshot_uses_latest_valid_quote_and_counts_invalid():
    result = build_synchronized_snapshot(
        {"spot": [q("spot", 1_000_000_000), q("spot", 1_100_000_000, 101), q("wrong", 1_200_000_000)]},
        now_ns=1_500_000_000, required_instruments=["spot"],
    )
    assert result["quotes"]["spot"]["price"] == 101
    assert result["invalid_observation_count"] == 1
    assert result["aligned"] is True


def test_snapshot_validates_clock_and_limits():
    try:
        build_synchronized_snapshot({}, now_ns=0)
    except ValueError as exc:
        assert "now_ns" in str(exc)
    else:
        raise AssertionError("invalid clock must be rejected")
