"""Year-aware NSE session-calendar factories for cash and stock futures."""

from __future__ import annotations

from datetime import datetime, time, timezone

from .market_session_calendar import MarketSessionCalendar
from .nse_2025_holidays import (
    NSE_EQUITY_TRADING_HOLIDAYS_2025,
    NSE_FNO_TRADING_HOLIDAYS_2025,
    NSE_MUHURAT_TRADING_DATES_2025,
)
from .nse_2026_holidays import (
    NSE_EQUITY_TRADING_HOLIDAYS_2026,
    NSE_FNO_TRADING_HOLIDAYS_2026,
    NSE_MUHURAT_TRADING_DATES_2026,
)


SUPPORTED_YEARS = frozenset({2025, 2026})


def supported_years() -> frozenset[int]:
    """Return the NSE holiday-calendar years currently bundled with the engine."""
    return SUPPORTED_YEARS


def _calendar_for_year(exchange: str, year: int) -> MarketSessionCalendar:
    if year == 2025:
        equity_holidays, fno_holidays = NSE_EQUITY_TRADING_HOLIDAYS_2025, NSE_FNO_TRADING_HOLIDAYS_2025
        # NSE circular NSE/CMTR/70319 and NSE/FAOP/70320 define the 2025
        # Muhurat normal-market session as 13:45-14:45 IST. The date remains
        # in the ordinary holiday set, so this explicit special session must
        # override that holiday when generating completeness windows.
        muhurat_dates = NSE_MUHURAT_TRADING_DATES_2025
        muhurat_session = (time(13, 45), time(14, 45))
    elif year == 2026:
        equity_holidays, fno_holidays = NSE_EQUITY_TRADING_HOLIDAYS_2026, NSE_FNO_TRADING_HOLIDAYS_2026
        # NSE has announced the 2026 Muhurat date, but its exact session
        # timing has not yet been published. Keep it fail-closed rather than
        # inventing a completeness window.
        muhurat_dates = NSE_MUHURAT_TRADING_DATES_2026
        muhurat_session = None
    else:
        supported = ", ".join(str(value) for value in sorted(SUPPORTED_YEARS))
        raise ValueError(f"unsupported NSE calendar year: {year}; bundled years: {supported}")
    if exchange == "NSE":
        special_sessions = (
            {day: muhurat_session for day in muhurat_dates}
            if muhurat_session is not None
            else {}
        )
        return MarketSessionCalendar(
            holidays=equity_holidays,
            weekday_start=time(9, 15),
            weekday_end=time(15, 29),
            special_sessions=special_sessions,
        )
    if exchange == "NFO":
        special_sessions = (
            {day: muhurat_session for day in muhurat_dates}
            if muhurat_session is not None
            else {}
        )
        return MarketSessionCalendar(
            holidays=fno_holidays,
            weekday_start=time(9, 15),
            weekday_end=time(15, 39),
            special_sessions=special_sessions,
        )
    raise ValueError(f"unsupported NSE instrument exchange: {exchange}")


def nse_equity_2026_calendar() -> MarketSessionCalendar:
    return _calendar_for_year("NSE", 2026)


def nse_stock_future_2026_calendar() -> MarketSessionCalendar:
    return _calendar_for_year("NFO", 2026)


def nse_calendar_for_instrument_2026(instrument: str) -> MarketSessionCalendar:
    """Backward-compatible 2026 factory."""
    return _calendar_for_year(instrument.split(":", 1)[0].upper(), 2026)


def _calendar_windows(start: datetime, end: datetime, exchange: str) -> tuple:
    """Build year-aware windows without applying one year's holidays to another."""
    if end < start:
        raise ValueError("end must not precede start")
    result = []
    for year in range(start.year, end.year + 1):
        year_start = max(start, datetime(year, 1, 1, tzinfo=timezone.utc))
        year_end = min(end, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        if year_start <= year_end:
            result.extend(_calendar_for_year(exchange, year).sessions(year_start, year_end))
    return tuple(result)


def nse_session_windows(request: object) -> tuple:
    """Build year-aware completeness windows for an intraday request."""
    if getattr(request, "timeframe") == "1d":
        return tuple()
    start = datetime.fromtimestamp(getattr(request, "start_ns") / 1_000_000_000, tz=timezone.utc)
    end = datetime.fromtimestamp(getattr(request, "end_ns") / 1_000_000_000, tz=timezone.utc)
    exchange = getattr(request, "instrument").split(":", 1)[0].upper()
    return _calendar_windows(start, end, exchange)


def nse_daily_timestamps(request: object) -> tuple[int, ...]:
    """Return one expected daily candle timestamp per valid trading session."""
    if getattr(request, "timeframe") != "1d":
        raise ValueError("daily timestamp planning requires timeframe='1d'")
    start = datetime.fromtimestamp(getattr(request, "start_ns") / 1_000_000_000, tz=timezone.utc)
    end = datetime.fromtimestamp(getattr(request, "end_ns") / 1_000_000_000, tz=timezone.utc)
    exchange = getattr(request, "instrument").split(":", 1)[0].upper()
    return tuple(window.start_ns for window in _calendar_windows(start, end, exchange))


def nse_session_windows_2026(request: object) -> tuple:
    """Backward-compatible alias; requests may still be year-spanning."""
    return nse_session_windows(request)
