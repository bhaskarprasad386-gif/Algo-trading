from __future__ import annotations

from datetime import date, time

import pytest

from app.backtesting.continuous_futures_acquisition import (
    build_continuous_futures_acquisition_plan,
    build_continuous_futures_gap_plan,
)
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.trading_calendar import TradingCalendar

INTERVAL_NS = 60 * 1_000_000_000


def _calendar() -> TradingCalendar:
    return TradingCalendar(session_open=time(9, 15), session_close=time(9, 16))


def _gap_windows() -> tuple[FNORolloverWindow, ...]:
    return (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 5), date(2026, 1, 5)),
    )


def test_acquisition_plan_rejects_rollover_identity_mismatch() -> None:
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),
        FNORolloverWindow("XYZ", "STOCK_FUTURE", "FEB", date(2026, 1, 5), date(2026, 1, 5)),
    )
    with pytest.raises(ValueError, match="does not match requested contract identity"):
        build_continuous_futures_acquisition_plan(
            windows,
            source="fake",
            timeframe="1m",
            interval_ns=INTERVAL_NS,
            calendar=_calendar(),
            max_request_ns=10 * INTERVAL_NS,
        )


def test_acquisition_plan_rejects_rollover_gap() -> None:
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 2), date(2026, 1, 2)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 6), date(2026, 1, 6)),
    )
    with pytest.raises(ValueError, match="gap or overlap"):
        build_continuous_futures_acquisition_plan(
            windows,
            source="fake",
            timeframe="1m",
            interval_ns=INTERVAL_NS,
            calendar=_calendar(),
            max_request_ns=10 * INTERVAL_NS,
        )


def test_gap_plan_uses_same_rollover_validation() -> None:
    windows = _gap_windows()
    with pytest.raises(ValueError, match="gap or overlap"):
        build_continuous_futures_gap_plan(
            HistoricalCatalog(),
            (
                windows[0],
                FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 6), date(2026, 1, 6)),
            ),
            source="fake",
            timeframe="1m",
            interval_ns=INTERVAL_NS,
            calendar=_calendar(),
            max_request_ns=10 * INTERVAL_NS,
        )
