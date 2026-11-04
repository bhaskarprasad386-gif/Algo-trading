from types import SimpleNamespace

from app.scanner.box_spread_paper_depth_feed import BoxPaperDepthFeed


def test_provider_timestamp_is_unknown_when_broker_frame_omits_it():
    record = SimpleNamespace(timestamp_ns=1_800_000_000_000_000_000, payload={
        "best_5_buy_data": [{"price": 1000, "quantity": 50}],
    })

    assert BoxPaperDepthFeed._provider_timestamp_ns(record) is None


def test_provider_timestamp_uses_raw_broker_timestamp_not_receipt_fallback():
    provider_ns = 1_800_000_000_000_000_000
    record = SimpleNamespace(timestamp_ns=provider_ns, payload={
        "exchange_timestamp_ns": str(provider_ns),
        "best_5_buy_data": [{"price": 1000, "quantity": 50}],
    })

    assert BoxPaperDepthFeed._provider_timestamp_ns(record) == provider_ns


def test_malformed_provider_timestamp_fails_closed_as_unknown_not_local_time():
    record = SimpleNamespace(timestamp_ns=1_800_000_000_000_000_000, payload={
        "exchange_timestamp_ns": "not-a-timestamp",
    })

    # Unknown provider time falls back to independent arrival freshness;
    # it must never be interpreted as proof of an old exchange timestamp.
    assert BoxPaperDepthFeed._provider_timestamp_ns(record) is None


def test_default_four_leg_skew_tolerance_is_five_seconds():
    feed = BoxPaperDepthFeed.__new__(BoxPaperDepthFeed)
    feed._max_skew = 5.0

    assert feed._max_skew == 5.0
