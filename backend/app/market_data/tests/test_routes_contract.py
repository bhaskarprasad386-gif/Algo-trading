from fastapi.testclient import TestClient

from app.main import app, _summarize_primary_exchange_feed


def test_health_endpoint_contract():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "database" in payload



def test_runtime_feed_health_does_not_let_unrelated_exchange_mask_missing_nse_nfo():
    summary = _summarize_primary_exchange_feed({
        "ticks_received": 1000,
        "last_exchange_tick_received_age_seconds_by_exchange_type": {"3": 0.2, "4": 0.1},
    })
    assert summary["status"] == "WAITING_FOR_PRIMARY_EXCHANGE_TICKS"
    assert summary["age_seconds"] is None
    assert summary["exchange_freshness"]["NSE"]["status"] == "NO_DATA"
    assert summary["exchange_freshness"]["NFO"]["status"] == "NO_DATA"


def test_runtime_feed_health_uses_oldest_required_exchange_age():
    summary = _summarize_primary_exchange_feed({
        "ticks_received": 1000,
        "last_exchange_tick_received_age_seconds_by_exchange_type": {"1": 0.5, "2": 2.4, "3": 0.1},
    })
    assert summary["status"] == "LIVE"
    assert summary["age_seconds"] == 2.4
    assert summary["exchange_freshness"]["NSE"]["status"] == "LIVE"
    assert summary["exchange_freshness"]["NFO"]["status"] == "LIVE"


def test_runtime_feed_health_marks_a_stale_required_exchange():
    summary = _summarize_primary_exchange_feed({
        "ticks_received": 1000,
        "last_exchange_tick_received_age_seconds_by_exchange_type": {"1": 0.2, "2": 8.0},
    })
    assert summary["status"] == "STALE"
    assert summary["age_seconds"] == 8.0
    assert summary["exchange_freshness"]["NFO"]["status"] == "STALE"
