"""NSE 2026 session-calendar factories for cash and stock futures."""

from __future__ import annotations

from datetime import date, datetime, time

from .market_session_calendar import MarketSessionCalendar
from .nse_2026_holidays import (
    NSE_EQUITY_TRADING_HOLIDAYS_2026,
    NSE_FNO_TRADING_HOLIDAYS_2026,
)


def nse_equity_2026_calendar() -> MarketSessionCalendar:
    return MarketSessionCalendar(
        holidays=NSE_EQUITY_TRADING_HOLIDAYS_2026,
        weekday_start=time(9, 15),
        weekday_end=time(15, 29),
    )


def nse_stock_future_2026_calendar() -> MarketSessionCalendar:
    return MarketSessionCalendar(
        holidays=NSE_FNO_TRADING_HOLIDAYS_2026,
        weekday_start=time(9, 15),
        weekday_end=time(15, 39),
    )


def nse_calendar_for_instrument_2026(instrument: str) -> MarketSessionCalendar:
    """Return the correct 2026 regular session calendar from an instrument key.

    NSE cash keys normally start with ``NSE:``, while NFO stock-future keys
    start with ``NFO:``. Unknown exchanges fail closed instead of guessing.
    """
    exchange = instrument.split(":", 1)[0].upper()
    if exchange == "NSE":
        return nse_equity_2026_calendar()
    if exchange == "NFO":
        return nse_stock_future_2026_calendar()
    raise ValueError(f"unsupported NSE instrument exchange: {exchange}")


def nse_session_windows_2026(request: object) -> tuple:
    """Build completeness windows for an intraday historical request.

    Daily data has no 09:15 session cadence, so it is deliberately excluded
    from this intraday completeness adapter until a daily-session rule exists.
    """
    timeframe = getattr(request, "timeframe")
    if timeframe == "1d":
        return tuple()
    start_ns = getattr(request, "start_ns")
    end_ns = getattr(request, "end_ns")
    start = datetime.fromtimestamp(start_ns / 1_000_000_000)
    end = datetime.fromtimestamp(end_ns / 1_000_000_000)
    return nse_calendar_for_instrument_2026(getattr(request, "instrument")).sessions(start, end)
