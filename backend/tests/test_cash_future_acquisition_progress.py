from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.backtesting.cash_future_historical_acquisition import (
    CashFutureAcquisitionProgress,
    CashFutureHistoricalAcquisitionService,
)


def test_progress_callback_reports_initial_and_completed_passes(make_service):
    service: CashFutureHistoricalAcquisitionService = make_service()
    events: list[CashFutureAcquisitionProgress] = []
    session = make_service.session
    start = datetime(2026, 1, 29, tzinfo=timezone.utc)
    end = start + timedelta(minutes=2)

    result = service.acquire(
        spot_instrument="NSE:3045:SBIN",
        exchange="NFO",
        underlying="SBIN",
        start=start,
        end=end,
        spot_sessions=(session,),
        future_sessions={"NFO:101:SBINJAN": (session,)},
        timeframe="1m",
        mode="CURRENT",
        retry_attempts=1,
        max_repair_passes=2,
        on_progress=events.append,
    )

    assert events
    assert events[0].pass_index == 0
    assert events[0].pending_chunks >= 0
    assert all(event.coverage is not None for event in events)
    assert events[-1].pass_index <= 2
    assert len(result.progress) == len(events)
    assert result.execution.results == ()
