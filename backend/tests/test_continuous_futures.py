from datetime import date

from app.backtesting.continuous_futures import build_continuous_futures_series
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalRecord


def _record(token: str, timestamp_ns: int, close: float) -> HistoricalRecord:
    return HistoricalRecord(
        source="test",
        instrument=f"NFO:{token}",
        timeframe="1m",
        timestamp_ns=timestamp_ns,
        payload={"close": close},
    )


def test_continuous_series_uses_each_real_contract_only_inside_its_window():
    jan_window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 28), date(2026, 1, 29))
    feb_window = FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 30), date(2026, 2, 2))
    jan = _record("JAN", 1769585400 * 1_000_000_000, 100.0)
    feb = _record("FEB", 1769758200 * 1_000_000_000, 101.0)

    series = build_continuous_futures_series(
        (jan_window, feb_window),
        {"JAN": (jan,), "FEB": (feb,)},
    )

    assert [item.contract_token for item in series] == ["JAN", "FEB"]
    assert [item.record.payload["close"] for item in series] == [100.0, 101.0]


def test_continuous_series_does_not_create_rollover_candle_for_missing_data():
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 29), date(2026, 1, 30))
    series = build_continuous_futures_series((window,), {"JAN": ()})

    assert series == ()


def test_continuous_series_excludes_records_outside_contract_window():
    window = FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 29), date(2026, 1, 29))
    before = _record(1769500000 and "JAN", 1769500000 * 1_000_000_000, 99.0)
    inside = _record("JAN", 1769671800 * 1_000_000_000, 100.0)
    after = _record("JAN", 1769760000 * 1_000_000_000, 101.0)

    series = build_continuous_futures_series((window,), {"JAN": (before, inside, after)})

    assert [item.record.payload["close"] for item in series] == [100.0]
    assert [item.contract_token for item in series] == ["JAN"]
