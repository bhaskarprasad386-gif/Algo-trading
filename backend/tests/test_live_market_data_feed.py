from app.backtesting.historical_catalog import HistoricalCatalog
from app.market_data.live_feed import LiveMarketDataFeed


class FakeEngine:
    def __init__(self):
        self.started = None
        self.callback = None
        self.closed = False

    def start(self, **kwargs):
        self.started = kwargs
        self.callback = kwargs["on_tick"]

    def subscribe(self, tokens, mode=None):
        return None

    def unsubscribe(self, tokens):
        return None

    def latest(self, symbol):
        return None

    def snapshot(self):
        return {}

    def close(self):
        self.closed = True


def test_live_feed_sends_one_tick_to_storage_and_strategy(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    engine = FakeEngine()
    seen = []
    feed = LiveMarketDataFeed(catalog, engine=engine, on_tick=seen.append)

    feed.start(exchange_type=1, tokens=["101"], mode=3)

    engine.callback({"token": "101", "symbol": "SBIN-EQ", "ltp": 800})
    assert seen[0]["symbol"] == "SBIN-EQ"
    assert feed.recorder.pending_count() == 1
    assert feed.flush() == 1

    rows = catalog.events(
        source="angelone-live",
        instrument="SBIN-EQ|101",
        timeframe="tick",
    )
    assert len(rows) == 1
    assert engine.started["exchange_type"] == 1
    assert engine.started["mode"] == 3
    catalog.close()


def test_live_feed_close_flushes_before_socket_close(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    engine = FakeEngine()
    feed = LiveMarketDataFeed(catalog, engine=engine)

    feed.start(exchange_type=2, tokens=["202"], mode=4)
    engine.callback({"token": "202", "symbol": "SBINFUT", "ltp": 801})
    feed.close()

    assert engine.closed is True
    assert catalog.count(source="angelone-live", instrument="SBINFUT|202", timeframe="tick") == 1
    catalog.close()
