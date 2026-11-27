from pathlib import Path


def test_retired_calendar_and_synthetic_live_routers_are_not_mounted():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")

    assert "app.include_router(calendar_spread_scanner_router)" not in source
    assert "app.include_router(live_synthetic_router)" not in source
    assert "app.include_router(live_synthetic_trace_router)" not in source
    assert '"/api/v1/scanner/synthetic-cash-carry/universe"' not in source


def test_historical_backtesting_routers_remain_registered():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")

    assert "app.include_router(cash_future_strategy_router)" in source
    assert "app.include_router(calendar_spread_strategy_router)" in source
