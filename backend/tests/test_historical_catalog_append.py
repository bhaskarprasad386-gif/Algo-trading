from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def test_upcoming_data_is_appended_without_replacing_history():
    catalog = HistoricalCatalog()
    first = HistoricalRecord("angel", "NIFTY", "1s", 1_000_000_000, {"close": 100})
    upcoming = HistoricalRecord("angel", "NIFTY", "1s", 2_000_000_000, {"close": 101})

    assert catalog.append([first]) == 1
    assert catalog.append([upcoming]) == 1
    assert [r.timestamp_ns for r in catalog.records(source="angel", instrument="NIFTY", timeframe="1s")] == [1_000_000_000, 2_000_000_000]
    assert catalog.watermark(source="angel", instrument="NIFTY", timeframe="1s") == 2_000_000_000


def test_upcoming_duplicate_is_not_downloaded_twice():
    catalog = HistoricalCatalog()
    record = HistoricalRecord("angel", "NIFTY", "1s", 1_000_000_000, {"close": 100})

    assert catalog.append([record]) == 1
    assert catalog.append([record]) == 0
    assert catalog.count(instrument="NIFTY") == 1


def test_late_gap_repair_is_added_and_watermark_stays_at_latest():
    catalog = HistoricalCatalog()
    catalog.append([
        HistoricalRecord("angel", "NIFTY", "1s", 1_000_000_000, {"close": 100}),
        HistoricalRecord("angel", "NIFTY", "1s", 3_000_000_000, {"close": 102}),
    ])
    assert catalog.watermark(source="angel", instrument="NIFTY", timeframe="1s") == 3_000_000_000

    assert catalog.append([
        HistoricalRecord("angel", "NIFTY", "1s", 2_000_000_000, {"close": 101})
    ]) == 1
    assert catalog.gaps(source="angel", instrument="NIFTY", timeframe="1s", interval_ns=1_000_000_000) == ()
    assert catalog.watermark(source="angel", instrument="NIFTY", timeframe="1s") == 3_000_000_000
