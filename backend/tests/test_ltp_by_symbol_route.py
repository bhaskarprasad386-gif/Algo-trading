from fastapi.testclient import TestClient

from app.main import app


def test_ltp_by_symbol_route_registered():
    paths = app.openapi().get("paths", {})
    assert "/api/v1/market-data/ltp-by-symbol" in paths


def test_ltp_by_symbol_requires_symbol():
    client = TestClient(app)
    response = client.get("/api/v1/market-data/ltp-by-symbol")
    assert response.status_code == 422



def test_cache_ltp_reads_fresh_common_feed_snapshot_without_broker(monkeypatch):
    from app.market_data import shared_cache

    class FakeCache:
        def __init__(self):
            self.requested_key = None

        def get(self, key):
            self.requested_key = key
            return {
                "ltp": 123.45,
                "timestamp_ns": 1_800_000_000_000_000_000,
                "symbol": "TEST-EQ",
            }

    cache = FakeCache()
    monkeypatch.setattr(shared_cache, "get_shared_market_data_cache", lambda: cache)
    response = TestClient(app).get(
        "/api/v1/market-data/cache-ltp",
        params={"exchange": "nse", "symboltoken": "101"},
    )

    assert response.status_code == 200
    assert response.json()["ltp"] == 123.45
    assert response.json()["source"] == "common-market-data-cache"
    assert cache.requested_key == "NSE:NSE:101"


def test_cache_ltp_returns_404_on_cache_miss(monkeypatch):
    from app.market_data import shared_cache

    class EmptyCache:
        def get(self, key):
            return None

    monkeypatch.setattr(shared_cache, "get_shared_market_data_cache", lambda: EmptyCache())
    response = TestClient(app).get(
        "/api/v1/market-data/cache-ltp",
        params={"exchange": "NFO", "symboltoken": "202"},
    )

    assert response.status_code == 404
