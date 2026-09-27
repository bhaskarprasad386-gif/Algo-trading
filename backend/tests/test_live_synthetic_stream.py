from app.market_data.live_synthetic_stream import (
    LiveSyntheticOptionFutureRecorder,
    SyntheticSubscription,
)


def test_synthetic_subscription_rejects_invalid_instrument_class():
    try:
        LiveSyntheticOptionFutureRecorder(
            ":memory:",
            [SyntheticSubscription(2, "1", "NIFTYFUT", "NIFTY", "OTHER")],
        )
    except ValueError as exc:
        assert "STOCK or INDEX" in str(exc)
    else:
        raise AssertionError("expected invalid instrument class rejection")


def test_synthetic_subscription_deduplicates_exchange_token():
    subscriptions = [
        SyntheticSubscription(2, "1", "NIFTYFUT", "NIFTY", "INDEX"),
        SyntheticSubscription(2, "1", "NIFTYFUT", "NIFTY", "INDEX"),
        SyntheticSubscription(2, "2", "NIFTYCE", "NIFTY", "INDEX", "2026-12-31", "CE", 25000, 75),
    ]
    collector = LiveSyntheticOptionFutureRecorder(":memory:", subscriptions)
    assert len(collector.subscriptions) == 2


def test_market_open_excludes_weekends_and_outside_session():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    assert LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 10, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 27, 10, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 9, 0, tzinfo=ist))
