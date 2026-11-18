"""Regression coverage for independent Cash-Future rollover chains."""

from datetime import date

from app.backtesting.fno_rollover import FNORolloverWindow, validate_futures_rollover_chain


def test_multi_underlying_windows_validate_independently() -> None:
    windows = (
        FNORolloverWindow("AAA", "STOCK_FUTURE", "101", date(2026, 1, 1), date(2026, 1, 29)),
        FNORolloverWindow("AAA", "STOCK_FUTURE", "102", date(2026, 1, 30), date(2026, 2, 27)),
        FNORolloverWindow("BBB", "STOCK_FUTURE", "201", date(2026, 1, 1), date(2026, 1, 29)),
        FNORolloverWindow("BBB", "STOCK_FUTURE", "202", date(2026, 1, 30), date(2026, 2, 27)),
    )

    groups = {}
    for window in windows:
        groups.setdefault((window.underlying, window.instrument_type), []).append(window)

    for (underlying, instrument_type), group in groups.items():
        validate_futures_rollover_chain(
            group,
            underlying=underlying,
            instrument_type=instrument_type,
        )
