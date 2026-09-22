from app.backtesting.engine_provenance import (
    CALENDAR_IDENTITY,
    ENGINE_COMPATIBILITY_VERSION,
    NSE_CALENDAR_IDENTITY,
    nse_calendar_hash,
    execution_implementation_hash,
    portfolio_accounting_hash,
    trading_calendar_hash,
)


def test_engine_compatibility_identity_is_explicit() -> None:
    assert ENGINE_COMPATIBILITY_VERSION == "universal-backtest-engine:v1"


def test_execution_implementation_hash_is_stable() -> None:
    first = execution_implementation_hash()
    second = execution_implementation_hash()
    assert first == second
    assert len(first) == 64


def test_portfolio_accounting_hash_is_stable_and_distinct() -> None:
    first = portfolio_accounting_hash()
    second = portfolio_accounting_hash()
    assert first == second
    assert len(first) == 64
    assert first != execution_implementation_hash()


def test_nse_calendar_identity_is_stable_and_versioned() -> None:
    first = nse_calendar_hash()
    second = nse_calendar_hash()
    assert first == second
    assert len(first) == 64
    assert NSE_CALENDAR_IDENTITY == "nse-session-calendar:2025-2026:v1"
    assert first != trading_calendar_hash()


def test_trading_calendar_identity_is_stable() -> None:
    first = trading_calendar_hash()
    second = trading_calendar_hash()
    assert first == second
    assert len(first) == 64
    assert CALENDAR_IDENTITY == "trading-calendar:generic:v1"