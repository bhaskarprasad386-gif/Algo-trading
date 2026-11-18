import pytest

from app.scanner import routes


def test_cash_future_live_route_uses_common_feed_snapshot(monkeypatch):
    calls = {}

    def snapshot(*, max_age_seconds, limit):
        calls["max_age_seconds"] = max_age_seconds
        calls["limit"] = limit
        return [
            {"symbol": "ABC", "contract_month": "CURRENT", "gap_pct": 1.25},
            {"symbol": "OTHER", "contract_month": "CURRENT", "gap_pct": 2.0},
        ]

    monkeypatch.setattr(routes, "_live_cash_future_snapshot_provider", snapshot)

    response = routes.cash_future_live_scanner(symbols="abc", db=None)

    assert calls == {"max_age_seconds": 5.0, "limit": 50}
    assert response["status"] == "success"
    assert response["mode"] == "live"
    assert response["count"] == 1
    assert response["data"] == [{"symbol": "ABC", "contract_month": "CURRENT", "gap_pct": 1.25}]


def test_cash_future_live_route_returns_503_when_not_wired(monkeypatch):
    monkeypatch.setattr(routes, "_live_cash_future_snapshot_provider", None)

    with pytest.raises(Exception) as exc_info:
        routes.cash_future_live_scanner(symbols="ABC", db=None)

    assert exc_info.value.status_code == 503
