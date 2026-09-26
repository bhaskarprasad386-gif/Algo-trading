from datetime import datetime, timezone

import pytest

from app.backtesting.calendar_spread_strategy_routes import (
    CalendarSpreadPointRequest,
    CalendarSpreadRunRequest,
    strategy_run,
)


def point(ts: str, *, near_bid=99.0, near_ask=100.0, far_bid=103.0, far_ask=104.0):
    return CalendarSpreadPointRequest(
        timestamp=datetime.fromisoformat(ts),
        underlying="SBIN",
        near_expiry=20260930,
        far_expiry=20261029,
        near_bid=near_bid,
        near_ask=near_ask,
        far_bid=far_bid,
        far_ask=far_ask,
        lot_size=100,
        strike=850.0,
        option_type="CALL",
    )


def test_calendar_spread_request_validates_near_far_and_direction():
    request = CalendarSpreadRunRequest(
        points=[point("2026-09-24T09:15:00+00:00")],
        direction="LONG_NEAR_SHORT_FAR",
    )
    assert request.strategy_id == "calendar-spread"

    with pytest.raises(ValueError):
        CalendarSpreadRunRequest(
            points=[point("2026-09-24T09:15:00+00:00")],
            direction="INVALID",
        )


def test_calendar_spread_strategy_run_reconciles_executable_edge_and_fees(tmp_path, monkeypatch):
    from app.core.config import settings

    db = tmp_path / "calendar-results.sqlite"
    monkeypatch.setattr(settings, "BACKTEST_RESULT_LEDGER_DB", str(db))

    result = strategy_run(
        CalendarSpreadRunRequest(
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point(
                    "2026-09-24T09:15:01+00:00",
                    near_bid=102.0,
                    near_ask=103.0,
                    far_bid=98.0,
                    far_ask=99.0,
                ),
            ],
            fees_per_unit=0.5,
        )
    )

    assert result["status"] == "success"
    assert result["completed_trades"] == 1
    assert result["unresolved_trades"] == 0
    # Entry edge = 103 - 100 = 3; exit reverse edge = 102 - 99 = 3.
    # Gross = (3 + 3) * 100 = 600; two-leg fees = 0.5 * 2 * 100 = 100.
    assert result["net_profit"] == 500.0
    assert result["trade_count"] == 1
    assert result["trades"][0]["gross_pnl"] == 600.0
    assert result["trades"][0]["fees"] == 100.0


def test_calendar_spread_strategy_run_respects_exact_timestamp_window(tmp_path, monkeypatch):
    from app.core.config import settings

    db = tmp_path / "calendar-window.sqlite"
    monkeypatch.setattr(settings, "BACKTEST_RESULT_LEDGER_DB", str(db))

    result = strategy_run(
        CalendarSpreadRunRequest(
            start_timestamp=datetime(2026, 9, 24, 9, 15, 1, tzinfo=timezone.utc),
            end_timestamp=datetime(2026, 9, 24, 9, 15, 2, tzinfo=timezone.utc),
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point("2026-09-24T09:15:01+00:00"),
                point(
                    "2026-09-24T09:15:02+00:00",
                    near_bid=102.0,
                    near_ask=103.0,
                    far_bid=98.0,
                    far_ask=99.0,
                ),
                point("2026-09-24T09:15:03+00:00"),
            ],
        )
    )

    assert result["completed_trades"] == 1
    assert result["start_timestamp"].startswith("2026-09-24T09:15:01")
    assert result["end_timestamp"].startswith("2026-09-24T09:15:02")


def test_calendar_spread_replay_exposes_only_source_supported_timeframes():
    from app.backtesting.calendar_spread_strategy_routes import calendar_spread_replay

    result = calendar_spread_replay(
        __import__("app.backtesting.calendar_spread_strategy_routes", fromlist=["CalendarSpreadReplayRequest"]).CalendarSpreadReplayRequest(
            trading_date=datetime(2026, 9, 24, tzinfo=timezone.utc).date(),
            underlying="SBIN",
            timeframe="1s",
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point("2026-09-24T09:15:01+00:00"),
                point("2026-09-24T09:15:02+00:00"),
            ],
        )
    )

    assert result["source_min_interval_seconds"] == 1.0
    assert result["available_replay_intervals"] == [
        "1s", "30s", "1m", "5m", "15m", "30m", "1h"
    ]
    assert result["count"] == 3


def test_calendar_spread_replay_minute_source_does_not_claim_1s():
    from app.backtesting.calendar_spread_strategy_routes import (
        CalendarSpreadReplayRequest,
        calendar_spread_replay,
    )

    result = calendar_spread_replay(
        CalendarSpreadReplayRequest(
            trading_date=datetime(2026, 9, 24, tzinfo=timezone.utc).date(),
            underlying="SBIN",
            timeframe="1m",
            points=[
                point("2026-09-24T09:15:00+00:00"),
                point("2026-09-24T09:16:00+00:00"),
            ],
        )
    )

    assert result["source_min_interval_seconds"] == 60.0
    assert result["available_replay_intervals"] == [
        "1m", "5m", "15m", "30m", "1h"
    ]
