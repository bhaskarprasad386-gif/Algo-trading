from app.core.config import settings
from app.market_data import routes


def test_shoonya_health_reports_disabled_without_runtime(monkeypatch):
    monkeypatch.setattr(settings, "SHOONYA_MARKET_DATA_ENABLED", False)
    monkeypatch.setattr(routes, "_shoonya_snapshot_provider", None)
    result = routes.get_shoonya_health()
    assert result["enabled"] is False
    assert result["state"] == "DISABLED"
    assert result["order_routing_enabled"] is False


def test_shoonya_health_reports_uninitialized_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "SHOONYA_MARKET_DATA_ENABLED", True)
    monkeypatch.setattr(routes, "_shoonya_snapshot_provider", None)
    result = routes.get_shoonya_health()
    assert result["enabled"] is True
    assert result["state"] == "NOT_INITIALIZED"
    assert result["strategy_fanout_enabled"] is False


def test_shoonya_health_uses_sanitized_runtime_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "SHOONYA_MARKET_DATA_ENABLED", True)
    expected = {
        "broker": "shoonya",
        "mode": "shadow_only",
        "state": "IDLE_NO_SUBSCRIPTIONS",
        "order_routing_enabled": False,
        "strategy_fanout_enabled": False,
    }
    monkeypatch.setattr(routes, "_shoonya_snapshot_provider", lambda: expected)
    assert routes.get_shoonya_health() == expected
