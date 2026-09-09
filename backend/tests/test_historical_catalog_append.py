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


def test_stock_future_one_second_history_and_upcoming_data_share_one_store():
    catalog = HistoricalCatalog()
    instrument = "NFO:FUTSTK:ABC26SEP:101"
    historical = HistoricalRecord(
        "angel", instrument, "1s", 1_000_000_000,
        {"close": 100, "expiry": "2026-09-24"},
    )
    upcoming = HistoricalRecord(
        "angel", instrument, "1s", 2_000_000_000,
        {"close": 101, "expiry": "2026-09-24"},
    )

    assert catalog.append([historical]) == 1
    assert catalog.append([upcoming]) == 1
    records = catalog.records(source="angel", instrument=instrument, timeframe="1s")
    assert [r.timestamp_ns for r in records] == [1_000_000_000, 2_000_000_000]
    assert [r.payload["expiry"] for r in records] == ["2026-09-24", "2026-09-24"]
    assert catalog.watermark(source="angel", instrument=instrument, timeframe="1s") == 2_000_000_000


def test_stock_future_expiry_tokens_are_never_mixed():
    catalog = HistoricalCatalog()
    sep = "NFO:FUTSTK:ABC26SEP:101"
    oct_ = "NFO:FUTSTK:ABC26OCT:102"
    catalog.append([
        HistoricalRecord("angel", sep, "1s", 1_000_000_000, {"close": 100, "expiry": "2026-09-24"}),
        HistoricalRecord("angel", oct_, "1s", 1_000_000_000, {"close": 110, "expiry": "2026-10-29"}),
    ])

    sep_records = catalog.records(source="angel", instrument=sep, timeframe="1s")
    oct_records = catalog.records(source="angel", instrument=oct_, timeframe="1s")
    assert len(sep_records) == 1
    assert len(oct_records) == 1
    assert sep_records[0].payload["expiry"] == "2026-09-24"
    assert oct_records[0].payload["expiry"] == "2026-10-29"
