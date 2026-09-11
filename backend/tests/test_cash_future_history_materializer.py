from datetime import date, datetime, timezone

from app.backtesting.cash_future_download_queue import CashFutureDownloadQueue, CashFutureSegmentDownload
from app.backtesting.cash_future_history_materializer import materialize_cash_future_history
from app.backtesting.cash_future_rollover_plan import CashFutureSegment
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_ingest import HistoricalFetchRequest
from app.backtesting.contract_master import ContractRecord
from app.scanner.cash_future_history_store import read_history


def _request(instrument: str, start_ns: int, end_ns: int) -> HistoricalFetchRequest:
    return HistoricalFetchRequest("angelone", instrument, "1m", start_ns, end_ns)


def test_materializer_synchronizes_common_timestamps_without_forward_fill(db_session, tmp_path):
    start = int(datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    minute = 60 * 1_000_000_000
    end = start + 2 * minute
    cash_instrument = "NSE:123:ABC-EQ"
    future_instrument = "NFO:999:ABC26OCT"
    catalog = HistoricalCatalog(tmp_path / "history.db")
    catalog.ingest(
        [
            HistoricalRecord("angelone", cash_instrument, "1m", start, {"close": 100.0}),
            HistoricalRecord("angelone", cash_instrument, "1m", start + minute, {"close": 101.0}),
            HistoricalRecord("angelone", cash_instrument, "1m", end, {"close": 102.0}),
            HistoricalRecord("angelone", future_instrument, "1m", start, {"close": 110.0, "volume": 10, "open_interest": 20}),
            HistoricalRecord("angelone", future_instrument, "1m", end, {"close": 111.0, "volume": 11, "open_interest": 21}),
        ]
    )
    contract = ContractRecord(
        "NFO", "ABC26OCT", "999", date(2026, 10, 29), "STOCK_FUTURE", "ABC", 125
    )
    segment = CashFutureSegment(date(2026, 9, 10), date(2026, 9, 10), contract)
    queue = CashFutureDownloadQueue(
        spot=_request(cash_instrument, start, end),
        futures=(CashFutureSegmentDownload(segment, _request(future_instrument, start, end)),),
    )

    inserted = materialize_cash_future_history(
        db=db_session,
        catalog=catalog,
        queue=queue,
        symbol="ABC",
        batch_size=1,
    )

    assert inserted == 2
    rows = read_history(db_session, "ABC", "2026-10")
    assert [row.gap for row in rows] == [10.0, 9.0]
    assert [row.lot_size for row in rows] == [125, 125]
    assert [row.oi for row in rows] == [20.0, 21.0]
    assert all(row.margin_required == 0.0 for row in rows)

    repeated = materialize_cash_future_history(
        db=db_session,
        catalog=catalog,
        queue=queue,
        symbol="ABC",
        batch_size=1,
    )
    assert repeated == 2
    assert len(read_history(db_session, "ABC", "2026-10")) == 2
