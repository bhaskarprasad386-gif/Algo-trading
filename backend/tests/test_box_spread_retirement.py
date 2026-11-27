from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def test_box_spread_has_no_production_runtime_or_feed_owner():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert "LIVE_BOX_SPREAD_RUNNER_FORCE_DISABLED = True" in source
    assert "CommonWebSocketManager(" not in source
    assert "MarketDataWebSocket(" not in source
    assert "asyncio.create_task(_live_box_spread_loop())" not in source
    assert 'app.include_router(live_box_spread_router)' not in source
    assert 'app.include_router(live_box_spread_websocket_router)' not in source
    assert '@app.get("/api/v1/scanner/box-spread/index-selection")' not in source
    assert '@app.post("/api/v1/scanner/box-spread/index-selection")' not in source
    assert '"box_spread": {"status": "DISABLED", "feed": "NONE", "runner": "NONE"}' in source
    assert '"box_spread": {"count":' not in source


def test_box_spread_and_retired_scanners_have_no_public_channels_or_alerts():
    shell = (REPO / "frontend" / "components" / "app-shell.tsx").read_text(encoding="utf-8")
    dashboard = (REPO / "frontend" / "app" / "page.tsx").read_text(encoding="utf-8")
    page_push = (ROOT / "app" / "scanner" / "page_push_websocket.py").read_text(encoding="utf-8")
    alerts = (ROOT / "app" / "alert_routes.py").read_text(encoding="utf-8")

    assert 'href: "/strategies/box-spread"' not in shell
    assert 'pathname.startsWith("/strategies/box-spread")' not in shell
    assert 'href: "/strategies/box-spread"' not in dashboard
    assert not (REPO / "frontend" / "app" / "strategies" / "box-spread" / "page.tsx").exists()
    channel_block = page_push.split("PAGE_CHANNELS = frozenset({", 1)[1].split("})", 1)[0]
    for retired in ("box-spread", "cash-future", "calendar-spread", "synthetic-arbitrage"):
        assert f'"{retired}"' not in channel_block
    assert "STRATEGIES = set()" in alerts
    assert 'history_models = {}' in alerts
