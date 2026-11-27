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
    assert "app.include_router(box_spread_paper_router)" not in source
    assert "app.include_router(box_spread_paper_private_ws_router)" not in source
    assert "app.include_router(box_spread_paper_rule_router)" not in source
    assert "app.include_router(page_push_websocket_router)" in source


def test_generic_backtest_run_results_and_market_replay_remain():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert "app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))" in source
    assert "app.include_router(create_download_status_router(backtest_status_store))" in source
    assert "app.include_router(create_universal_result_router(universal_result_service))" in source


def test_only_shared_index_runner_is_admitted_to_production_websocket():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    feed_source = Path(__file__).parents[1].joinpath(
        "app", "market_data", "common_strategy_feed.py"
    ).read_text(encoding="utf-8")

    assert "LIVE_BOX_SPREAD_RUNNER_FORCE_DISABLED = False" in source
    assert "box_shared_feed_manager.attach()" in source
    assert "SharedIndexRunnerLocalFeedManager" in source
    assert "_shared_index_market_data_recovery_loop()" in source
    assert "_cash_future_history_loop())" not in source
    assert "_live_cash_future_loop())" not in source
    assert "_live_calendar_spread_loop())" not in source
    assert "_live_synthetic_loop())" not in source
    assert "SharedIndexOnlyWebSocketManager" in feed_source
    assert "SharedIndexMarketDataRunner (NIFTY/BANKNIFTY)" in feed_source
    assert "CommonWebSocketManager(" not in source
    assert "MarketDataWebSocket(" not in source
    assert "shared_common_manager()" not in source
    assert "SharedIndexRunnerLocalFeedManager(shared_index_market_data_runner)" in source
    assert "box_shared_feed_manager.attach()" in source
    assert "shared_feed_manager=box_shared_feed_manager" in source
    assert "ShoonyaMarketDataSupervisor(" not in source
    assert "SHOONYA_MARKET_DATA_ENABLED and shoonya_market_data_supervisor" not in source
    assert "shoonya_market_data_supervisor" not in source
    market_routes = Path(__file__).parents[1].joinpath("app", "market_data", "routes.py").read_text(encoding="utf-8")
    assert "/shoonya/subscriptions" not in market_routes
    assert "/shoonya-health" not in market_routes
    assert "ShoonyaSubscriptionRequest" not in market_routes
    assert "/api/v1/market-data/live-cash-future/health" not in source
    runtime_start = source.index("def market_data_runtime_health")
    runtime_end = source.index("def runtime_diagnostic_errors", runtime_start)
    runtime_health = source[runtime_start:runtime_end]
    assert "register_normalized_observer" not in runtime_health
    assert "register_transport_observer" not in runtime_health
    assert '"cash_future"' not in runtime_health
    assert '"calendar_spread"' not in runtime_health
    assert '"synthetic_arbitrage"' not in runtime_health
