from pathlib import Path


def _main_source() -> str:
    return Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")


def test_retired_calendar_and_synthetic_live_routers_are_not_mounted():
    source = _main_source()

    assert "app.include_router(calendar_spread_scanner_router)" not in source
    assert "app.include_router(live_synthetic_router)" not in source
    assert "app.include_router(live_synthetic_trace_router)" not in source
    assert '"/api/v1/scanner/synthetic-cash-carry/universe"' not in source
    assert 'configure_page_push_snapshot("calendar-spread"' not in source
    assert 'configure_page_push_snapshot("synthetic-arbitrage"' not in source


def test_box_and_shared_page_push_routes_remain_registered():
    source = _main_source()

    assert "app.include_router(live_box_spread_router)" in source
    assert "app.include_router(box_spread_paper_router)" in source
    assert "app.include_router(page_push_websocket_router)" in source


def test_historical_backtesting_routers_remain_registered():
    source = _main_source()

    assert "app.include_router(cash_future_strategy_router)" in source
    assert "app.include_router(calendar_spread_strategy_router)" in source
