from datetime import date

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord
from app.backtesting.historical_download_executor import ResumableHistoricalExecutor
from app.backtesting.historical_ingest import HistoricalIngestionService
from app.backtesting.historical_session_repair import HistoricalSessionRepairService
from app.backtesting.trading_calendar import TradingCalendar


NS_PER_MINUTE = 60 * 1_000_000_000


def _ts(day: date, minute: int) -> int:
    return TradingCalendar().session_for(day).start_ns + minute * NS_PER_MINUTE


class RepairSource:
    def __init__(self):
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        yield HistoricalRecord(
            request.source,
            request.instrument,
            request.timeframe,
            request.start_ns,
            {"close": 100},
        )


def test_session_repair_executes_only_same_session_gaps():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar()
    day = date(2026, 9, 7)
    catalog.ingest([
        HistoricalRecord("cash", "ABC", "1m", _ts(day, 0), {"close": 100}),
        HistoricalRecord("cash", "ABC", "1m", _ts(day, 3), {"close": 103}),
    ])
    source = RepairSource()
    service = HistoricalSessionRepairService(
        ResumableHistoricalExecutor(HistoricalIngestionService(catalog), collect_results=False)
    )

    report = service.repair(
        source,
        catalog=catalog,
        calendar=calendar,
        source_name="cash",
        instrument="ABC",
        timeframe="1m",
        interval_ns=NS_PER_MINUTE,
        start_date=day,
        end_date=day,
        max_request_ns=NS_PER_MINUTE,
    )

    assert report.completed
    assert len(report.plan.requests) == 2
    assert [(r.start_ns, r.end_ns) for r in source.requests] == [
        (_ts(day, 1), _ts(day, 1)),
        (_ts(day, 2), _ts(day, 2)),
    ]
    assert catalog.timestamps(
        source="cash", instrument="ABC", timeframe="1m",
        start_ns=_ts(day, 0), end_ns=_ts(day, 3),
    ) == (_ts(day, 0), _ts(day, 1), _ts(day, 2), _ts(day, 3))
    catalog.close()


def test_session_repair_is_noop_when_no_gap_exists():
    catalog = HistoricalCatalog()
    calendar = TradingCalendar()
    day = date(2026, 9, 7)
    catalog.ingest([
        HistoricalRecord("cash", "ABC", "1m", _ts(day, 0), {"close": 100}),
        HistoricalRecord("cash", "ABC", "1m", _ts(day, 1), {"close": 101}),
    ])
    source = RepairSource()
    service = HistoricalSessionRepairService(
        ResumableHistoricalExecutor(HistoricalIngestionService(catalog), collect_results=False)
    )

    report = service.repair(
        source,
        catalog=catalog,
        calendar=calendar,
        source_name="cash",
        instrument="ABC",
        timeframe="1m",
        interval_ns=NS_PER_MINUTE,
        start_date=day,
        end_date=day,
        max_request_ns=10 * NS_PER_MINUTE,
    )

    assert report.plan.requests == ()
    assert report.execution.completed_chunks == 0
    assert source.requests == []
    catalog.close()
