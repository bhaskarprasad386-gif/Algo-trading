from datetime import date, datetime, timezone

from app.backtesting.continuous_futures import build_continuous_futures_series_from_catalog
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def _ns(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_continuous_futures_catalog_replay_switches_contracts_without_cross_contract_contamination(tmp_path):
    catalog_path = tmp_path / "history.sqlite"
    catalog = HistoricalCatalog(catalog_path)
    for token, timestamp in (
        ("101", "2026-01-29T09:15:00"),
        ("101", "2026-01-29T09:16:00"),
        ("102", "2026-02-26T09:15:00"),
        ("102", "2026-02-26T09:16:00"),
        ("103", "2026-03-26T09:15:00"),
        ("103", "2026-03-26T09:16:00"),
    ):
        catalog.upsert(
            HistoricalRecord(
                source="fake",
                instrument=f"NFO:{token}",
                timeframe="1m",
                timestamp_ns=_ns(timestamp),
                payload={"close": float(token)},
            )
        )

    windows = (
        FNORolloverWindow("SBIN", "STOCK_FUTURE", "101", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("SBIN", "STOCK_FUTURE", "102", date(2026, 2, 26), date(2026, 2, 26)),
        FNORolloverWindow("SBIN", "STOCK_FUTURE", "103", date(2026, 3, 26), date(2026, 3, 26)),
    )

    series = build_continuous_futures_series_from_catalog(
        catalog,
        windows,
        source="fake",
        timeframe="1m",
    )

    assert tuple(item.contract_token for item in series) == ("101", "101", "102", "102", "103", "103")
    assert tuple(item.payload["close"] for item in series) == (101.0, 101.0, 102.0, 102.0, 103.0, 103.0)
    assert all(previous.timestamp_ns < current.timestamp_ns for previous, current in zip(series, series[1:]))
    catalog.close()
