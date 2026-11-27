from pathlib import Path


def test_cash_future_calendar_and_synthetic_live_apis_are_not_mounted():
    root = Path(__file__).parents[1]
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    shell = (root.parent / "frontend" / "components" / "app-shell.tsx").read_text(encoding="utf-8")
    dashboard = (root.parent / "frontend" / "app" / "page.tsx").read_text(encoding="utf-8")

    for retired in ("cash-future", "calendar-spread", "synthetic-arbitrage", "synthetic-future-cash-carry", "full-fno"):
        assert f'"id": "{retired}"' not in main
        assert f'"{retired}"' not in shell
        assert f'"{retired}"' not in dashboard
    for retired_router in ("scanner_router", "auto_scanner_router", "monthly_results_router", "cash_future_strategy_router", "calendar_spread_strategy_router", "full_fno_router"):
        assert f"app.include_router({retired_router})" not in main
    assert "CashFutureDownloadManager" not in main
    assert "app.scanner.backtest_jobs" not in main
    assert "recover_interrupted_jobs" not in main
    assert "_history_collector_task" not in main
    assert "_iv_option_snapshot_task" not in main


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
    mobile = root.parent / "mobile" / "android" / "app" / "src" / "main"
    for relative in (
        "java/com/algotrading/app/MainActivity.kt",
        "java/com/algotrading/app/ApiService.kt",
        "AndroidManifest.xml",
    ):
        source = (mobile / relative).read_text(encoding="utf-8")
        assert "BoxSpread" not in source
        assert "box_spread" not in source.lower()
        assert "box-spread" not in source.lower()


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
    assert '"commodity-market-data"' not in feed_source
    assert not (root / "app" / "market_data" / "engine.py").exists()
    assert not (root / "app" / "market_data" / "live_feed.py").exists()
    assert not (root / "app" / "market_data" / "live_commodity_stream.py").exists()
    assert not (root / "app" / "market_data" / "commodity_subscriptions.py").exists()


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


def test_retired_strategy_configuration_is_not_exposed():
    root = Path(__file__).parents[1]
    config = (root / "app" / "core" / "config.py").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")

    retired_settings = (
        "CASH_FUTURE_HISTORY_ENABLED", "CASH_FUTURE_HISTORY_SYMBOLS",
        "CASH_FUTURE_HISTORY_INTERVAL_SECONDS", "LIVE_CASH_FUTURE_DATA_ENABLED",
        "LIVE_CASH_FUTURE_DAILY_SHARDS_ENABLED", "LIVE_CALENDAR_SPREAD_DATA_ENABLED",
        "LIVE_CALENDAR_SPREAD_MIN_GAP_POINTS", "LIVE_CALENDAR_SPREAD_MIN_GROSS_PROFIT",
        "LIVE_CALENDAR_SPREAD_CAPITAL", "LIVE_CALENDAR_SPREAD_RESULT_RETENTION_DAYS",
        "LIVE_SYNTHETIC_DATA_ENABLED", "LIVE_SYNTHETIC_MIN_ARBITRAGE_POINTS",
        "LIVE_SYNTHETIC_CARRY_RATE", "LIVE_SYNTHETIC_FEES_PER_UNIT",
        "LIVE_SYNTHETIC_RESULT_RETENTION_DAYS", "LIVE_BOX_SPREAD_DATA_ENABLED",
        "LIVE_BOX_SPREAD_DEPTH_SHADOW_ENABLED", "LIVE_CASH_FUTURE_ALERT_MIN_GAP_PCT",
        "LIVE_CASH_FUTURE_SYMBOLS", "LIVE_CASH_FUTURE_PAIR_TOLERANCE_SECONDS",
        "LIVE_CASH_FUTURE_MAX_QUOTE_AGE_SECONDS", "LIVE_CASH_FUTURE_ALERT_COOLDOWN_SECONDS",
        "LIVE_CASH_FUTURE_COST_BPS", "LIVE_CASH_FUTURE_SLIPPAGE_BPS",
    )
    for setting in retired_settings:
        assert setting not in config
        assert setting not in env_example

    workflows = root.parent / ".github" / "workflows"
    for workflow in workflows.glob("*.yml"):
        source = workflow.read_text(encoding="utf-8")
        for setting in retired_settings:
            assert setting not in source, f"{workflow.name} still references {setting}"

    # Shared market-data persistence remains available for non-retired data services.
    assert "LIVE_MARKET_DATA_PERSISTENCE_ENABLED" in config


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
    assert "backtest_jobs" not in migrations  # Existing rows are preserved, but startup no longer creates or alters this retired table.
    assert "DROP TABLE backtest_jobs" not in migrations
    assert "backtest_job_result_chunks" not in migrations
    assert "live_synthetic_alert_history" not in migrations

    for retired_path in (
        "cash_future_history.py", "live_cash_future_alert_history.py",
        "live_cash_future_scanner_result.py", "live_calendar_spread_alert_history.py",
        "live_calendar_spread_scanner_result.py", "live_synthetic_alert_history.py",
        "live_synthetic_pair_history.py", "synthetic_trace_opportunity.py",
        "synthetic_trace_status_check.py", "live_box_spread_alert_history.py",
        "live_box_spread_snapshot.py", "live_box_spread_snapshot_key.py",
        "backtest_job_result_chunk.py", "backtest_job.py",
    ):
        assert not (root / "app" / "models" / retired_path).exists()

    for retired_path in (
        "scanner/backtest_jobs.py", "scanner/full_fno_backtest.py",
        "scanner/synchronized_replay.py", "scanner/routes.py",
        "backtesting/monthly_results_routes.py", "backtesting/routes.py",
        "scanner/result_chunk_store.py", "market_data/historical_sync_jobs.py",
        "backtest/result_store.py", "models/backtest_job_result_chunk.py",
    ):
        assert not (root / "app" / retired_path).exists()


def test_retired_full_fno_paper_backtest_has_no_android_entrypoint_or_api():
    root = Path(__file__).parents[1]
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "full_fno_router" not in main
    assert "full-fno" not in main
    mobile = root.parent / "mobile" / "android" / "app" / "src" / "main"
    main_activity = (mobile / "java" / "com" / "algotrading" / "app" / "MainActivity.kt").read_text(encoding="utf-8")
    api = (mobile / "java" / "com" / "algotrading" / "app" / "ApiService.kt").read_text(encoding="utf-8")
    manifest = (mobile / "AndroidManifest.xml").read_text(encoding="utf-8")
    home_layout = (mobile / "res" / "layout" / "activity_main.xml").read_text(encoding="utf-8")

    for source in (main_activity, api, manifest, home_layout):
        assert "FullFno" not in source
        assert "full-fno" not in source
    assert not (mobile / "java" / "com" / "algotrading" / "app" / "FullFnoBacktestActivity.kt").exists()
    assert not (mobile / "res" / "layout" / "activity_full_fno_backtest.xml").exists()


def test_iv_runtime_has_no_direct_websocket_fallbacks():
    root = Path(__file__).parents[1]
    runtime = (root / "app" / "scanner" / "iv_skew_flip" / "runtime.py").read_text(encoding="utf-8")
    package_init = (root / "app" / "scanner" / "iv_skew_flip" / "__init__.py").read_text(encoding="utf-8")
    main = (root / "app" / "main.py").read_text(encoding="utf-8")

    assert "future_feed" not in package_init
    assert "CommonStrategyMarketFeed(" not in runtime
    assert "future_feed:" not in runtime
    assert not (root / "app" / "scanner" / "iv_skew_flip" / "future_feed.py").exists()
    assert "IVSharedRunnerSpotFeedAdapter" in runtime
    assert "IVSharedRunnerOptionFeedAdapter" in runtime
    assert "_DisabledIVMarketDataTransport" in runtime
    assert "SHARED_RUNNER_REQUIRED" in runtime
    assert "cannot accept a direct feed manager" in runtime
    assert "download_iv_instruments" not in runtime
    assert "instrument_rows_snapshot" in runtime
    assert "download_iv_instruments" not in main
    assert "instrument_rows_snapshot" in (root / "app" / "market_data" / "shared_index_market_data_runner.py").read_text(encoding="utf-8")


def test_iv_has_no_independent_instrument_master_recovery_job_or_script():
    root = Path(__file__).parents[1]
    repository = root.parent
    assert not (repository / ".github" / "workflows" / "iv-master-cache.yml").exists()
    assert not (root / "scripts" / "recover_iv_master_artifact.py").exists()
    assert not (root / "scripts" / "recover_latest_master_artifacts.py").exists()
    integration_workflow = (repository / ".github" / "workflows" / "integration-tests.yml").read_text(encoding="utf-8")
    assert "iv-independent-master-network" not in integration_workflow
    assert "IV_INSTRUMENT_MASTER_CACHE_PATH" not in integration_workflow
    assert "iv-instrument-master-validated" not in integration_workflow
    assert "test_iv_skew_flip_instruments.py" not in integration_workflow

    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    runtime = (root / "app" / "scanner" / "iv_skew_flip" / "runtime.py").read_text(encoding="utf-8")
    assert "download_iv_instruments" not in main
    assert "download_iv_instruments" not in runtime
    assert "instrument_rows_snapshot" in runtime


def test_next_vps_bootstrap_removes_retired_iv_master_recovery_timer_and_secret():
    root = Path(__file__).parents[1]
    repository = root.parent
    bootstrap = (repository / "deploy" / "bootstrap-vps.sh").read_text(encoding="utf-8")

    assert "algo-trading-master-recovery.timer" in bootstrap
    assert "algo-trading-master-recovery.service" in bootstrap
    assert "/etc/algo-trading/master-recovery.env" in bootstrap
    assert "rm -f /etc/algo-trading/master-recovery.env" in bootstrap
    assert not (root / "scripts" / "install_master_recovery_timer.sh").exists()
