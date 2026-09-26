from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.high_resolution import EVENT_TIMEFRAME
from app.market_data.live_recorder import LiveMarketDataRecorder


def test_live_recorder_batches_and_persists_ticks(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    recorder = LiveMarketDataRecorder(catalog, batch_size=2)

    assert recorder.on_tick({"token": "101", "symbol": "SBIN-EQ", "ltp": 800}) == 0
    assert recorder.pending_count() == 1

    assert recorder.on_tick({"token": "101", "symbol": "SBIN-EQ", "ltp": 801}) == 2
    assert recorder.pending_count() == 0

    rows = catalog.events(
        source="angelone-live",
        instrument="SBIN-EQ|101",
        timeframe=EVENT_TIMEFRAME,
    )
    assert len(rows) == 2
    assert rows[0].payload["ltp"] == 800
    assert rows[1].payload["ltp"] == 801
    catalog.close()


def test_live_recorder_flushes_partial_batch(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    recorder = LiveMarketDataRecorder(catalog, batch_size=10)

    recorder.on_tick({"token": "202", "symbol": "SBINAPR26", "ltp": 802})
    assert recorder.flush() == 1
    assert recorder.flush() == 0

    rows = catalog.events(
        source="angelone-live",
        instrument="SBINAPR26|202",
        timeframe=EVENT_TIMEFRAME,
    )
    assert len(rows) == 1
    catalog.close()


def test_live_recorder_requires_an_instrument(tmp_path):
    catalog = HistoricalCatalog(str(tmp_path / "history.db"))
    recorder = LiveMarketDataRecorder(catalog)

    try:
        recorder.on_tick({"ltp": 800})
    except ValueError as exc:
        assert "token or symbol" in str(exc)
    else:
        raise AssertionError("missing instrument must be rejected")
    catalog.close()
