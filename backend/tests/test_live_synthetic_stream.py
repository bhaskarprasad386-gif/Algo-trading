from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_data.live_synthetic_stream import (
    LiveSyntheticOptionFutureRecorder,
    SyntheticSubscription,
)


def _collector():
    return LiveSyntheticOptionFutureRecorder(
        ":memory:",
        [SyntheticSubscription(
            2, "101", "NIFTY30SEP26CE", "NIFTY", "INDEX",
            "30SEP2026", "CE", 100.0, 75,
        )],
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
    ist = ZoneInfo("Asia/Kolkata")
    assert LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 10, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 27, 10, 0, tzinfo=ist))
    assert not LiveSyntheticOptionFutureRecorder.market_open(datetime(2026, 9, 28, 9, 0, tzinfo=ist))


def test_normalize_converts_ltp_and_best_bid_ask_without_fabrication():
    collector = _collector()
    meta = collector.subscriptions[0]
    payload = collector._normalize(
        {
            "token": "101",
            "last_traded_price": "12345",
            "best_5_buy_data": [{"price": "12340", "quantity": "10"}],
            "best_5_sell_data": [{"price": "12350", "quantity": "20"}],
        },
        meta,
    )
    assert payload["ltp"] == 123.45
    assert payload["bid"] == 123.40
    assert payload["ask"] == 123.50
    assert payload["bid_qty"] == 10.0
    assert payload["ask_qty"] == 20.0
    assert payload["strike"] == 100.0
    assert payload["lot_size"] == 75


def test_normalize_rejects_invalid_or_non_positive_quote_levels_to_none():
    collector = _collector()
    meta = collector.subscriptions[0]
    payload = collector._normalize(
        {
            "token": "101",
            "last_traded_price": "bad",
            "best_5_buy_data": [{"price": "0", "quantity": "0"}],
            "best_5_sell_data": [{"price": "-1", "quantity": "x"}],
        },
        meta,
    )
    assert payload["ltp"] is None
    assert payload["bid"] is None
    assert payload["ask"] is None
    assert payload["bid_qty"] is None
    assert payload["ask_qty"] is None


def test_stream_persists_previous_second_when_bucket_advances_and_flushes_final_bucket(tmp_path, monkeypatch):
    from app.market_data import live_synthetic_stream as stream

    collector = LiveSyntheticOptionFutureRecorder(
        str(tmp_path / "synthetic.db"),
        [SyntheticSubscription(
            2, "101", "NIFTY30SEP26CE", "NIFTY", "INDEX",
            "30SEP2026", "CE", 100.0, 75,
        )],
        batch_size=1,
        poll_seconds=0.05,
    )

    class FakeAuth:
        def login(self):
            return None

    observed = []

    def on_observation(payload):
        observed.append(payload["source_timestamp_ns"])
        collector.stop_event.set()

    collector.auth = FakeAuth()
    collector.on_observation = on_observation

    class FakeSocket:
        def __init__(self, *, auth):
            self.auth = auth

        def connect(self, **kwargs):
            on_data = kwargs["on_data"]
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "1000000000",
                "last_traded_price": "12345",
            })
            on_data({
                "token": "101",
                "symbol": "NIFTY30SEP26CE",
                "exchange_timestamp": "2000000000",
                "last_traded_price": "12355",
            })

        def close(self):
            return None

    monkeypatch.setattr(stream, "MarketDataWebSocket", FakeSocket)
    monkeypatch.setattr(
        stream.LiveSyntheticOptionFutureRecorder,
        "market_open",
        staticmethod(lambda now=None: True),
    )

    assert collector._run_session() == 2
    assert observed == [1_000_000_000]

    from app.backtesting.historical_catalog import HistoricalCatalog

    catalog = HistoricalCatalog(str(tmp_path / "synthetic.db"))
    try:
        records = catalog.records(
            source=collector.SOURCE,
            instrument="NIFTY30SEP26CE|101",
            timeframe="1s",
        )
    finally:
        catalog.close()

    assert [record.timestamp_ns for record in records] == [1_000_000_000, 2_000_000_000]
    assert [record.payload["ltp"] for record in records] == [123.45, 123.55]
    assert [record.payload["source_timestamp_ns"] for record in records] == [
        1_000_000_000,
        2_000_000_000,
    ]
