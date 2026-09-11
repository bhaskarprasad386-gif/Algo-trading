from datetime import date, datetime, timezone

from app.backtesting.cash_future_historical_loader import (
    CashFutureHistoricalLoader,
    CashFutureHistorySelection,
)
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


def _ns(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def test_loader_merges_cash_and_future_by_timestamp_and_uses_historical_lot(tmp_path):
    data = HistoricalCatalog(str(tmp_path / "data.db"))
    contracts = ContractMasterCatalog(str(tmp_path / "contracts.db"))
    snapshot = date(2026, 1, 2)
    contracts.upsert_snapshot(
        snapshot,
        [ContractRecord("NFO", "ABC26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "ABC", 75)],
    )
    data.ingest(
        [
            HistoricalRecord("angelone", "NSE:1:ABC", "1m", _ns("2026-01-05T03:45:00"), {"close": 100.0}),
            HistoricalRecord("angelone", "NSE:1:ABC", "1m", _ns("2026-01-05T03:46:00"), {"close": 101.0}),
            HistoricalRecord("angelone", "NFO:101:ABC26JANFUT", "1m", _ns("2026-01-05T03:45:00"), {"close": 102.0, "open_interest": 500}),
            HistoricalRecord("angelone", "NFO:101:ABC26JANFUT", "1m", _ns("2026-01-05T03:46:00"), {"close": 103.0, "open_interest": 501}),
        ]
    )
    loader = CashFutureHistoricalLoader(data, contracts)
    points = tuple(loader.iter_points(CashFutureHistorySelection("NSE:1:ABC", "NFO", "ABC", date(2026, 1, 5), date(2026, 1, 5))))

    assert len(points) == 2
    assert [p.gap for p in points] == [2.0, 2.0]
    assert all(p.lot_size == 75 for p in points)
    assert points[0].oi == 500
    assert points[0].contract_month == "2026-01"


def test_loader_does_not_emit_weekend_or_unmatched_timestamps(tmp_path):
    data = HistoricalCatalog(str(tmp_path / "data.db"))
    contracts = ContractMasterCatalog(str(tmp_path / "contracts.db"))
    contracts.upsert_snapshot(
        date(2026, 1, 2),
        [ContractRecord("NFO", "ABC26JANFUT", "101", date(2026, 1, 29), "STOCK_FUTURE", "ABC", 75)],
    )
    data.ingest(
        [
            HistoricalRecord("angelone", "NSE:1:ABC", "1m", _ns("2026-01-03T03:45:00"), {"close": 100}),
            HistoricalRecord("angelone", "NFO:101:ABC26JANFUT", "1m", _ns("2026-01-03T03:45:00"), {"close": 101}),
            HistoricalRecord("angelone", "NSE:1:ABC", "1m", _ns("2026-01-05T03:45:00"), {"close": 100}),
            HistoricalRecord("angelone", "NFO:101:ABC26JANFUT", "1m", _ns("2026-01-05T03:45:00"), {"close": 101}),
        ]
    )

    loader = CashFutureHistoricalLoader(data, contracts)
    points = tuple(loader.iter_points(CashFutureHistorySelection("NSE:1:ABC", "NFO", "ABC", date(2026, 1, 3), date(2026, 1, 5))))

    assert len(points) == 1
    assert points[0].timestamp.date() == date(2026, 1, 5)


def test_loader_rejects_invalid_range(tmp_path):
    data = HistoricalCatalog(str(tmp_path / "data.db"))
    contracts = ContractMasterCatalog(str(tmp_path / "contracts.db"))
    try:
        CashFutureHistorySelection("NSE:1:ABC", "NFO", "ABC", date(2026, 1, 6), date(2026, 1, 5))
    except ValueError as exc:
        assert "end_date" in str(exc)
    else:
        raise AssertionError("invalid range was accepted")
