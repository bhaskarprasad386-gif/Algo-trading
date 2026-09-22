"""Immutable implementation identities for backtest engine provenance."""

from __future__ import annotations

import inspect

from app.backtesting.execution import ExecutionSimulator
from app.backtesting.market_session_calendar import MarketSessionCalendar
from app.backtesting.nse_2025_holidays import (
    NSE_EQUITY_TRADING_HOLIDAYS_2025,
    NSE_FNO_TRADING_HOLIDAYS_2025,
    NSE_MUHURAT_TRADING_DATES_2025,
)
from app.backtesting.nse_2026_holidays import (
    NSE_EQUITY_TRADING_HOLIDAYS_2026,
    NSE_FNO_TRADING_HOLIDAYS_2026,
    NSE_MUHURAT_TRADING_DATES_2026,
)
from app.backtesting.portfolio import Portfolio
from app.backtesting.provenance import provenance_hash
from app.backtesting.trading_calendar import TradingCalendar


ENGINE_COMPATIBILITY_VERSION = "universal-backtest-engine:v1"
CALENDAR_IDENTITY = "trading-calendar:generic:v1"
NSE_CALENDAR_IDENTITY = "nse-session-calendar:2025-2026:v1"


def _source_hash(identity: str, implementation: object) -> str:
    try:
        source = inspect.getsource(implementation)
    except (OSError, TypeError) as exc:
        raise RuntimeError(f"unable to inspect implementation: {identity}") from exc
    return provenance_hash({"identity": identity, "source": source})


def execution_implementation_hash(implementation: object = ExecutionSimulator) -> str:
    return _source_hash("execution-simulator", implementation)


def portfolio_accounting_hash(implementation: object = Portfolio) -> str:
    return _source_hash("portfolio-accounting", implementation)


def trading_calendar_hash(implementation: object = TradingCalendar) -> str:
    return _source_hash(CALENDAR_IDENTITY, implementation)


def nse_calendar_hash() -> str:
    """Hash NSE calendar implementation plus bundled holiday/session data."""
    return provenance_hash({
        "identity": NSE_CALENDAR_IDENTITY,
        "market_session_calendar_source": inspect.getsource(MarketSessionCalendar),
        "nse_calendar_factory_source": inspect.getsource(
            __import__("app.backtesting.nse_session_calendars", fromlist=["_calendar_for_year"])._calendar_for_year
        ),
        "2025": {
            "equity_holidays": sorted(day.isoformat() for day in NSE_EQUITY_TRADING_HOLIDAYS_2025),
            "fno_holidays": sorted(day.isoformat() for day in NSE_FNO_TRADING_HOLIDAYS_2025),
            "muhurat_dates": sorted(day.isoformat() for day in NSE_MUHURAT_TRADING_DATES_2025),
        },
        "2026": {
            "equity_holidays": sorted(day.isoformat() for day in NSE_EQUITY_TRADING_HOLIDAYS_2026),
            "fno_holidays": sorted(day.isoformat() for day in NSE_FNO_TRADING_HOLIDAYS_2026),
            "muhurat_dates": sorted(day.isoformat() for day in NSE_MUHURAT_TRADING_DATES_2026),
        },
    })


__all__ = [
    "ENGINE_COMPATIBILITY_VERSION",
    "CALENDAR_IDENTITY",
    "NSE_CALENDAR_IDENTITY",
    "execution_implementation_hash",
    "portfolio_accounting_hash",
    "trading_calendar_hash",
    "nse_calendar_hash",
]