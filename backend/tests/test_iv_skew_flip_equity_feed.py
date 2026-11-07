from types import SimpleNamespace

import pytest

from app.market_data.contracts import InstrumentKey
from app.scanner.iv_skew_flip.equity_feed import (
    IVWeightedEquityFeedAdapter,
    WeightedEquity,
)


class FakeFeed:
    def __init__(self):
        self.descriptors = ()
        self.callback = None
        self.stopped = 0

    def stop(self):
        self.stopped += 1

    def start(self, descriptors, callback):
        self.descriptors = tuple(descriptors)
        self.callback = callback
        return tuple(item.key for item in self.descriptors)


class FakeMaster:
    def resolve_cash_instrument(self, symbol, exchange):
        assert exchange == "NSE"
        return {"token": f"token-{symbol}", "symbol": f"{symbol}-EQ"}


def make_adapter():
    feed = FakeFeed()
    adapter = IVWeightedEquityFeedAdapter(
        feed=feed, clock_ns=lambda: 10_000_000_000,
    )
    adapter.start(FakeMaster(), (
        WeightedEquity("HDFCBANK", 0.105),
        WeightedEquity("ICICIBANK", 0.083),
    ))
    return adapter, feed


def send(feed, descriptor, *, ts=10_000_000_000, ltp=100.0, payload=None, **kwargs):
    feed.callback(SimpleNamespace(
        instrument=descriptor.key,
        timestamp_ns=ts,
        ltp=ltp,
        bid=99.9,
        ask=100.1,
        bid_qty=10,
        ask_qty=12,
        volume=1000,
        payload={"exchange_timestamp_ns": ts} if payload is None else payload,
        **kwargs,
    ))


def test_equity_feed_subscribes_only_its_weighted_nse_cash_basket():
    adapter, feed = make_adapter()
    assert len(feed.descriptors) == 2
    assert all(item.exchange == "NSE" for item in feed.descriptors)
    assert all(item.instrument_type == "equity" for item in feed.descriptors)
    assert adapter.snapshot()["status"] == "WAITING_FOR_TICKS"


def test_equity_feed_requires_every_basket_member_fresh_before_signals():
    adapter, feed = make_adapter()
    send(feed, feed.descriptors[0])
    state = adapter.snapshot()
    assert state["status"] == "PARTIAL"
    assert state["signals_allowed"] is False
    send(feed, feed.descriptors[1])
    state = adapter.snapshot()
    assert state["status"] == "LIVE"
    assert state["signals_allowed"] is True
    assert state["quotes"]["HDFCBANK"]["weight"] == 0.105
    assert state["quotes"]["HDFCBANK"]["bid"] == 99.9


def test_equity_feed_rejects_stale_out_of_order_and_missing_source_timestamp():
    adapter, feed = make_adapter()
    descriptor = feed.descriptors[0]
    send(feed, descriptor, ts=6_000_000_000)
    # Older updates and records without provider source time must not refresh it.
    send(feed, descriptor, ts=5_000_000_000, ltp=90.0)
    send(feed, descriptor, ts=10_000_000_000, payload={})
    assert adapter.snapshot()["status"] == "STALE"


def test_equity_feed_rejects_invalid_basket_entries():
    adapter = IVWeightedEquityFeedAdapter(feed=FakeFeed())
    with pytest.raises(ValueError):
        adapter.start(FakeMaster(), ())
    with pytest.raises(ValueError):
        adapter.start(FakeMaster(), (
            WeightedEquity("HDFCBANK", 0.1),
            WeightedEquity("HDFCBANK", 0.2),
        ))
    with pytest.raises(ValueError):
        adapter.start(FakeMaster(), (WeightedEquity("HDFCBANK", float("nan")),))


def test_equity_feed_rejects_unsubscribed_tokens_and_invalid_prices():
    adapter, feed = make_adapter()
    feed.callback(SimpleNamespace(
        instrument=InstrumentKey("NSE", "NSE", "not-subscribed"),
        timestamp_ns=10_000_000_000, ltp=100.0,
        payload={"exchange_timestamp_ns": 10_000_000_000},
    ))
    send(feed, feed.descriptors[0], ltp=float("nan"))
    assert adapter.snapshot()["status"] == "WAITING_FOR_TICKS"


def test_equity_feed_stop_releases_only_its_adapter_state():
    adapter, feed = make_adapter()
    adapter.stop()
    assert adapter.snapshot()["status"] == "STOPPED"
    assert feed.stopped >= 2
