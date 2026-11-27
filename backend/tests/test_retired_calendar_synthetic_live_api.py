from pathlib import Path


def test_cash_future_calendar_and_synthetic_live_apis_are_not_mounted():
    root = Path(__file__).parents[1]
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    shell = (root.parent / "frontend" / "components" / "app-shell.tsx").read_text(encoding="utf-8")
    dashboard = (root.parent / "frontend" / "app" / "page.tsx").read_text(encoding="utf-8")

    for retired in ("cash-future", "calendar-spread", "synthetic-arbitrage", "synthetic-future-cash-carry"):
        assert f'"id": "{retired}"' not in main
        assert f'"{retired}"' not in shell
        assert f'"{retired}"' not in dashboard
    for retired_router in ("scanner_router", "auto_scanner_router", "monthly_results_router", "cash_future_strategy_router", "calendar_spread_strategy_router"):
        assert f"app.include_router({retired_router})" not in main
    assert "CashFutureDownloadManager" not in main


def test_box_spread_and_paper_trading_are_fully_retired():
    root = Path(__file__).parents[1]
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    page_push = (root / "app" / "scanner" / "page_push_websocket.py").read_text(encoding="utf-8")
    shell = (root.parent / "frontend" / "components" / "app-shell.tsx").read_text(encoding="utf-8")
    dashboard = (root.parent / "frontend" / "app" / "page.tsx").read_text(encoding="utf-8")

    assert "box_spread" not in main.lower()
    assert "box-spread" not in shell
    assert "box_spread" not in dashboard.lower()
    assert "box-spread" not in page_push
    assert "BoxSpread" not in main
    assert "LIVE_BOX_SPREAD_RUNNER_FORCE_DISABLED" not in main
    assert not (root.parent / "frontend" / "app" / "strategies" / "box-spread").exists()
    assert not (root.parent / "frontend" / "app" / "strategies" / "box-spread-paper").exists()


def test_common_broker_websocket_is_exclusively_owned_by_shared_index_runner():
    root = Path(__file__).parents[1]
    feed_source = (root / "app" / "market_data" / "common_strategy_feed.py").read_text(encoding="utf-8")
    runner_source = (root / "app" / "market_data" / "shared_index_market_data_runner.py").read_text(encoding="utf-8")
    main = (root / "app" / "main.py").read_text(encoding="utf-8")

    assert "class SharedIndexOnlyWebSocketManager" in feed_source
    assert '"shared-index-market-data:spot"' in feed_source
    assert '"shared-index-market-data:options"' in feed_source
    assert '"shared-index-market-data:futures"' in feed_source
    assert "LOCKED = (\"NIFTY\", \"BANKNIFTY\")" in runner_source
    assert "shared_index_market_data_runner = SharedIndexMarketDataRunner" in main
    assert "_shared_index_market_data_recovery_loop()" in main
    assert "box_shared_feed_manager" not in main
    assert "LiveBoxSpreadRunner" not in main
    assert "CommonWebSocketManager(" not in main
    assert "MarketDataWebSocket(" not in main


def test_iv_scanner_reads_shared_runner_without_owning_broker_subscriptions():
    root = Path(__file__).parents[1]
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    runtime = (root / "app" / "scanner" / "iv_skew_flip" / "runtime.py").read_text(encoding="utf-8")
    adapters = (root / "app" / "scanner" / "iv_skew_flip" / "shared_runner_feed.py").read_text(encoding="utf-8")

    assert "shared_runner=shared_index_market_data_runner" in main
    assert "IVSharedRunnerSpotFeedAdapter" in runtime
    assert "IVSharedRunnerOptionFeedAdapter" in runtime
    assert '"owns_subscriptions": False' in adapters
    assert "shared-index-market-data" in adapters
    assert "CommonStrategyMarketFeed(" not in main
    assert "MarketDataWebSocket(" not in main


def test_generic_historical_replay_routes_remain_independent():
    root = Path(__file__).parents[1]
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))" in main
    assert "app.include_router(create_download_status_router(backtest_status_store))" in main
    assert "app.include_router(create_universal_result_router(universal_result_service))" in main


def test_retired_strategy_models_and_full_fno_backtest_are_not_registered_or_importable():
    root = Path(__file__).parents[1]
    models = (root / "app" / "models" / "__init__.py").read_text(encoding="utf-8")
    init_db = (root / "app" / "core" / "init_db.py").read_text(encoding="utf-8")
    migrations = (root / "app" / "core" / "schema_migrations.py").read_text(encoding="utf-8")

    for retired in (
        "CashFutureHistory", "LiveCashFutureAlertHistory", "LiveCashFutureScannerResult",
        "LiveCalendarSpreadAlertHistory", "LiveCalendarSpreadScannerResult",
        "LiveSyntheticAlertHistory", "LiveSyntheticPairHistory",
        "LiveBoxSpreadAlertHistory", "LiveBoxSpreadSnapshot", "LiveBoxSpreadSnapshotKey",
        "BacktestJob", "BacktestJobResultChunk",
    ):
        assert retired not in models
    assert "cash_future" not in init_db.lower()
    assert "backtest_jobs" not in migrations
    assert "backtest_job_result_chunks" not in migrations
    assert "live_synthetic_alert_history" not in migrations

    for retired_path in (
        "cash_future_history.py", "live_cash_future_alert_history.py",
        "live_cash_future_scanner_result.py", "live_calendar_spread_alert_history.py",
        "live_calendar_spread_scanner_result.py", "live_synthetic_alert_history.py",
        "live_synthetic_pair_history.py", "synthetic_trace_opportunity.py",
        "synthetic_trace_status_check.py", "live_box_spread_alert_history.py",
        "live_box_spread_snapshot.py", "live_box_spread_snapshot_key.py",
        "backtest_job.py", "backtest_job_result_chunk.py",
    ):
        assert not (root / "app" / "models" / retired_path).exists()

    for retired_path in (
        "scanner/backtest_jobs.py", "scanner/full_fno_backtest.py",
        "scanner/synchronized_replay.py", "scanner/routes.py",
        "backtesting/monthly_results_routes.py", "backtesting/routes.py",
        "scanner/result_chunk_store.py",
    ):
        assert not (root / "app" / retired_path).exists()
