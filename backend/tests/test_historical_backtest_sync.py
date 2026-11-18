from datetime import datetime, timedelta

import pytest

from app.market_data import historical_backtest_sync as sync


class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or {
            "status": True,
            "data": [["2026-01-02T09:15:00", 100, 102, 99, 101, 5000]],
        }
        self.error = error

    def get_candles(self, exchange, token, interval, from_date, to_date):
        self.calls.append((exchange, token, interval, from_date, to_date))
        if self.error:
            raise self.error
        return self.response


def _instrument():
    return sync.HistoricalBacktestInstrument(
        symbol="RELIANCE",
        token="2885",
        exchange="NSE",
        segment="NSE",
        instrument_type="EQ",
    )


def test_sync_fetches_missing_range_and_records_half_open_coverage(monkeypatch):
    client = FakeClient()
    calls = []
    coverage = []
    monkeypatch.setattr(sync, "missing_ranges", lambda db, key, start, end: [(start, end)])
    monkeypatch.setattr(sync, "upsert_1m_bars", lambda db, bars: calls.append(list(bars)) or 1)
    monkeypatch.setattr(sync, "record_coverage", lambda db, **kwargs: coverage.append(kwargs))

    start = datetime(2026, 1, 2, 9, 15)
    end = datetime(2026, 1, 2, 9, 16)
    result = sync.sync_historical_backtest_data(
        object(), instrument=_instrument(), start=start, end=end, client=client
    )

    assert len(client.calls) == 1
    assert len(calls) == 1
    assert calls[0][0]["close"] == 101.0
    assert len(coverage) == 1
    assert coverage[0]["validated"] is True
    assert coverage[0]["start"] == start
    assert coverage[0]["end"] == end
    assert coverage[0]["row_count"] == 1
    assert result.completed and result.rows_written == 1


def test_progress_callback_reports_initial_and_chunk_completion(monkeypatch):
    client = FakeClient(response={
        "status": True,
        "data": [
            ["2026-01-02T09:15:00", 100, 102, 99, 101, 5000],
            ["2026-01-02T09:16:00", 101, 103, 100, 102, 5001],
        ],
    })
    progress = []
    monkeypatch.setattr(sync, "missing_ranges", lambda db, key, start, end: [
        (datetime(2026, 1, 2, 9, 15), datetime(2026, 1, 2, 9, 16)),
        (datetime(2026, 1, 2, 9, 16), datetime(2026, 1, 2, 9, 17)),
    ])
    monkeypatch.setattr(sync, "upsert_1m_bars", lambda db, bars: len(bars))
    monkeypatch.setattr(sync, "record_coverage", lambda db, **kwargs: None)

    result = sync.sync_historical_backtest_data(
        object(),
        instrument=_instrument(),
        start=datetime(2026, 1, 2, 9, 15),
        end=datetime(2026, 1, 2, 9, 17),
        client=client,
        progress_fn=lambda completed, total, rows: progress.append((completed, total, rows)),
    )

    assert progress == [(0, 2, 0), (1, 2, 1), (2, 2, 2)]
    assert result.completed and result.rows_written == 2


def test_contiguous_single_bar_coverage_ends_at_next_minute():
    start = datetime(2026, 1, 2, 9, 15)
    bars = [{"timestamp": start}]

    assert sync._contiguous_minute_ranges(bars) == [
        (start, datetime(2026, 1, 2, 9, 16))
    ]


def test_provider_range_is_chunked_at_30_day_boundary():
    start = datetime(2026, 1, 1, 9, 15)
    end = start + timedelta(days=31)

    assert sync._chunk_range(start, end) == [
        (start, start + timedelta(days=30)),
        (start + timedelta(days=30), end),
    ]


def test_sync_records_sparse_provider_response_as_separate_coverage_runs(monkeypatch):
    client = FakeClient(response={
        "status": True,
        "data": [
            ["2026-01-02T09:15:00", 100, 102, 99, 101, 5000],
            ["2026-01-02T09:17:00", 101, 103, 100, 102, 5001],
        ],
    })
    coverage = []
    monkeypatch.setattr(sync, "missing_ranges", lambda db, key, start, end: [(start, end)])
    monkeypatch.setattr(sync, "upsert_1m_bars", lambda db, bars: len(bars))
    monkeypatch.setattr(sync, "record_coverage", lambda db, **kwargs: coverage.append(kwargs))

    start = datetime(2026, 1, 2, 9, 15)
    end = datetime(2026, 1, 2, 9, 18)
    result = sync.sync_historical_backtest_data(
        object(), instrument=_instrument(), start=start, end=end, client=client
    )

    assert not result.completed
    assert result.rows_written == 2
    assert [(item["start"], item["end"], item["row_count"]) for item in coverage] == [
        (datetime(2026, 1, 2, 9, 15), datetime(2026, 1, 2, 9, 16), 1),
        (datetime(2026, 1, 2, 9, 17), datetime(2026, 1, 2, 9, 18), 1),
    ]


def test_sync_skips_provider_when_range_is_already_covered(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(sync, "missing_ranges", lambda *args: [])

    result = sync.sync_historical_backtest_data(
        object(),
        instrument=_instrument(),
        start=datetime(2026, 1, 2, 9, 15),
        end=datetime(2026, 1, 2, 9, 16),
        client=client,
    )

    assert client.calls == []
    assert result.completed and result.ranges_requested == 0


def test_sync_does_not_record_coverage_when_provider_fails(monkeypatch):
    client = FakeClient(error=RuntimeError("provider down"))
    monkeypatch.setattr(
        sync,
        "missing_ranges",
        lambda db, key, start, end: [(start, end)],
    )
    coverage = []
    monkeypatch.setattr(sync, "record_coverage", lambda *args, **kwargs: coverage.append(kwargs))

    with pytest.raises(RuntimeError, match="provider down"):
        sync.sync_historical_backtest_data(
            object(),
            instrument=_instrument(),
            start=datetime(2026, 1, 2, 9, 15),
            end=datetime(2026, 1, 2, 9, 16),
            client=client,
        )
    assert coverage == []


def test_sync_rejects_malformed_candle_before_coverage(monkeypatch):
    client = FakeClient(response={"status": True, "data": [["bad", 100, 102, 99, 101, 5000]]})
    monkeypatch.setattr(sync, "missing_ranges", lambda db, key, start, end: [(start, end)])
    monkeypatch.setattr(sync, "record_coverage", lambda *args, **kwargs: pytest.fail("coverage must not be recorded"))

    with pytest.raises(ValueError, match="timestamp"):
        sync.sync_historical_backtest_data(
            object(),
            instrument=_instrument(),
            start=datetime(2026, 1, 2, 9, 15),
            end=datetime(2026, 1, 2, 9, 16),
            client=client,
        )
