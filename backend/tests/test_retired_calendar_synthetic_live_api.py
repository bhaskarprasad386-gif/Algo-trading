from pathlib import Path


def test_retired_historical_routes_are_not_mounted_or_advertised():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert "app.include_router(scanner_router)" not in source
    assert "app.include_router(auto_scanner_router)" not in source
    assert "app.include_router(monthly_results_router)" not in source
    assert "app.include_router(cash_future_strategy_router)" not in source
    assert "app.include_router(calendar_spread_strategy_router)" not in source
    assert "CashFutureDownloadManager" not in source
    assert '"id": "cash-future"' not in source
    assert '"id": "calendar-spread"' not in source
    assert '"id": "synthetic-future-cash-carry"' not in source


def test_box_spread_and_full_fno_workspaces_remain():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert '"id": "box-spread"' in source
    assert '"id": "full-fno"' in source
    assert "app.include_router(live_box_spread_router)" in source
    assert "app.include_router(box_spread_paper_router)" in source
    assert "app.include_router(page_push_websocket_router)" in source


def test_generic_backtest_run_results_and_market_replay_remain():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert "app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))" in source
    assert "app.include_router(create_download_status_router(backtest_status_store))" in source
    assert "app.include_router(create_universal_result_router(universal_result_service))" in source
