"""Immutable implementation identities for backtest engine provenance."""

from __future__ import annotations

import inspect

from app.backtesting.execution import ExecutionSimulator
from app.backtesting.portfolio import Portfolio
from app.backtesting.provenance import provenance_hash
from app.backtesting.trading_calendar import TradingCalendar


ENGINE_COMPATIBILITY_VERSION = "universal-backtest-engine:v1"
CALENDAR_IDENTITY = "trading-calendar:generic:v1"


def _source_hash(identity: str, implementation: object) -> str:
    try:
        source = inspect.getsource(implementation)
    except (OSError, TypeError) as exc:
        raise RuntimeError(f"unable to inspect implementation: {identity}") from exc
    return provenance_hash({"identity": identity, "source": source})


def execution_implementation_hash() -> str:
    return _source_hash("execution-simulator", ExecutionSimulator)


def portfolio_accounting_hash() -> str:
    return _source_hash("portfolio-accounting", Portfolio)


def trading_calendar_hash() -> str:
    return _source_hash(CALENDAR_IDENTITY, TradingCalendar)


__all__ = [
    "ENGINE_COMPATIBILITY_VERSION",
    "CALENDAR_IDENTITY",
    "execution_implementation_hash",
    "portfolio_accounting_hash",
    "trading_calendar_hash",
]
