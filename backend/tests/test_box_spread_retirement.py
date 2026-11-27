from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def test_box_spread_has_no_production_runtime_or_feed_owner():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    for retired_runtime_symbol in (
        "LIVE_BOX_SPREAD_RUNNER_FORCE_DISABLED",
        "CommonWebSocketManager",
        "MarketDataWebSocket",
        "LiveBoxSpreadRunner",
        "BoxSpreadRunnerSupervisor",
        "box_isolated_feed_manager",
        "live_box_spread_runner",
        "_live_box_spread_loop",
        "configure_live_box_spread",
        "live_box_spread_router",
        "live_box_spread_websocket_router",
        "BoxSpreadIndexSelection",
        "download_box_spread_instruments",
        "evaluate_butterfly_condor_snapshot",
    ):
        assert retired_runtime_symbol not in source
    assert '"id": "box-spread"' not in source
    assert '"box_spread": {"status": "RETIRED"}' in source


def test_box_spread_and_retired_scanners_have_no_public_channels_or_alerts():
    shell = (REPO / "frontend" / "components" / "app-shell.tsx").read_text(encoding="utf-8")
    dashboard = (REPO / "frontend" / "app" / "page.tsx").read_text(encoding="utf-8")
    custom_alerts = (REPO / "frontend" / "app" / "custom-alert" / "page.tsx").read_text(encoding="utf-8")
    page_push = (ROOT / "app" / "scanner" / "page_push_websocket.py").read_text(encoding="utf-8")
    alerts = (ROOT / "app" / "alert_routes.py").read_text(encoding="utf-8")

    assert 'href: "/strategies/box-spread"' not in shell
    assert 'pathname.startsWith("/strategies/box-spread")' not in shell
    assert "/strategies/credit-butterfly-condor" not in shell
    assert 'href: "/strategies/box-spread"' not in dashboard
    assert "box_spread" not in dashboard
    assert "BoxSpread" not in custom_alerts
    assert "box-spread" not in custom_alerts
    assert not (REPO / "frontend" / "app" / "strategies" / "box-spread" / "page.tsx").exists()
    assert not (REPO / "frontend" / "components" / "strategy-workspace.tsx").exists()
    assert not (REPO / "frontend" / "app" / "strategies" / "credit-butterfly-condor" / "monthly" / "page.tsx").exists()
    assert not (REPO / "frontend" / "components" / "CreditButterflyCondorWorkspace.tsx").exists()
    channel_block = page_push.split("PAGE_CHANNELS = frozenset({", 1)[1].split("})", 1)[0]
    for retired in ("box-spread", "cash-future", "calendar-spread", "synthetic-arbitrage", "credit-butterfly-condor-weekly", "credit-butterfly-condor-monthly"):
        assert f'"{retired}"' not in channel_block
    assert "STRATEGIES = set()" in alerts
    assert 'history_models = {}' in alerts

    android_main = (REPO / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt").read_text(encoding="utf-8")
    android_manifest = (REPO / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert "BoxSpreadActivity" not in android_main
    assert "btnBoxSpread" not in android_main
    assert "BoxSpreadActivity" not in android_manifest
    assert "boxSpreadLive" not in (REPO / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt").read_text(encoding="utf-8")
    assert not (REPO / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "BoxSpreadActivity.kt").exists()
