from fastapi.testclient import TestClient
from app.scanner.vpt_x_reversal_routes import router, _history, _lock
import pytest
import time

@pytest.fixture(autouse=True)
def clear_signal_history():
    with _lock:
        _history.clear()
    yield
    with _lock:
        _history.clear()

# Exercise route contract with a local test app to avoid importing the full trading app.
from fastapi import FastAPI
app = FastAPI()
app.include_router(router)
client = TestClient(app)

def _ticks(prices, start=None):
    start = start if start is not None else int(time.time() * 1000) - 3_000
    return [
        {"timestamp_ms": start + i * 100, "price": p, "buy_volume": 35, "sell_volume": 18,
         "bid_qty": 100, "ask_qty": 45, "bid": p - .05, "ask": p + .05}
        for i, p in enumerate(prices)
    ]

def test_status_is_isolated_and_off():
    body = client.get("/api/v1/scanner/vpt-x/status").json()
    assert body["feed_connected"] is False and body["scanner_active"] is False
    assert body["order_routing_enabled"] is False

def test_client_clock_field_is_not_accepted_to_bypass_freshness():
    response = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY", "ticks": _ticks([100 + i * .1 for i in range(30)], start=1_000_000), "now_ms": 1_010_000})
    assert response.status_code == 200
    assert response.json()["status"] == "WAITING_DATA"

def test_request_schema_has_no_client_clock_field():
    from app.scanner.vpt_x_reversal_routes import SnapshotRequest
    assert "now_ms" not in SnapshotRequest.model_fields


def test_future_timestamp_snapshot_fails_closed():
    future_start = int(time.time() * 1000) + 10_000
    response = client.post("/api/v1/scanner/vpt-x/evaluate", json={
        "underlying": "NIFTY", "ticks": _ticks([100 + i * .1 for i in range(30)], start=future_start)
    })
    assert response.status_code == 200
    assert response.json()["status"] == "WAITING_DATA"
    assert response.json()["candidates"] == []


def test_invalid_underlying_rejected():
    assert client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "FINNIFTY"}).status_code == 422

def test_waiting_data_does_not_create_history():
    response = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY", "ticks": _ticks([100, 101, 102])}).json()
    assert response["status"] == "WAITING_DATA"
    assert client.get("/api/v1/scanner/vpt-x/signals").json()["count"] == 0

def test_evaluate_contract_and_bounded_signal_history():
    response = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "BANKNIFTY", "ticks": _ticks([100 + i * .1 for i in range(30)])}).json()
    assert response["status"] == "EVALUATED" and len(response["candidates"]) == 6
    assert {x["pattern"] for x in response["candidates"]} == {"SWING_EXHAUSTION", "COMPRESSION_REVERSAL", "V_REVERSAL"}
    assert {x["direction"] for x in response["candidates"]} == {"BUY", "SELL"}
    assert all(x["scanner_only"] and x["order_routing_enabled"] is False for x in response["candidates"])
    assert client.get("/api/v1/scanner/vpt-x/signals?underlying=BANKNIFTY").json()["count"] == 6

def test_idle_signal_transitions_to_expired():
    from app.scanner.vpt_x_reversal_routes import _cleanup, _history
    with _lock:
        _history["test:signal"] = {"status": "WATCH", "_updated_ms": 1, "underlying": "NIFTY"}
        _cleanup(100_000)
        assert _history["test:signal"]["status"] == "EXPIRED"


def _stub_eval(price, trigger=False, invalidation=99.0):
    return {
        "status": "EVALUATED", "features": {"price": price},
        "candidates": [{
            "pattern": "SWING_EXHAUSTION", "direction": "BUY", "score": 70,
            "strength": "DEVELOPING", "trigger_ready": trigger, "invalidation_price": invalidation,
            "components": {}, "evidence": [], "scanner_only": True, "order_routing_enabled": False
        }]
    }

def test_signal_identity_stable_for_duplicate_state(monkeypatch):
    import app.scanner.vpt_x_reversal_routes as routes
    clock = [10_000]
    monkeypatch.setattr(routes, "_now_ms", lambda: clock[0])
    monkeypatch.setattr(routes, "evaluate_vptx", lambda snapshot, now_ms=None: _stub_eval(100, False))
    first = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "nifty"}).json()["candidates"][0]
    clock[0] += 1_000
    second = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
    assert first["signal_id"] == second["signal_id"]
    assert first["created_at"] == second["created_at"]
    assert second["status"] == "WATCH"

def test_triggered_signal_invalidates_and_does_not_reactivate(monkeypatch):
    import app.scanner.vpt_x_reversal_routes as routes
    clock = [20_000]
    states = [{"price": 101, "trigger": True, "invalidation": 99}, {"price": 98, "trigger": True, "invalidation": 90}, {"price": 101, "trigger": True, "invalidation": 90}]
    monkeypatch.setattr(routes, "_now_ms", lambda: clock[0])
    monkeypatch.setattr(routes, "evaluate_vptx", lambda snapshot, now_ms=None: _stub_eval(states[0]["price"], states[0]["trigger"], states[0]["invalidation"]))
    triggered = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
    assert triggered["status"] == "TRIGGERED"
    states.pop(0)
    clock[0] += 100
    monkeypatch.setattr(routes, "evaluate_vptx", lambda snapshot, now_ms=None: _stub_eval(states[0]["price"], states[0]["trigger"], states[0]["invalidation"]))
    invalidated = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
    assert invalidated["status"] == "INVALIDATED"
    assert invalidated["invalidation_price"] == 99.0  # prior trigger level is immutable
    states.pop(0)
    clock[0] += 100
    monkeypatch.setattr(routes, "evaluate_vptx", lambda snapshot, now_ms=None: _stub_eval(states[0]["price"], states[0]["trigger"]))
    recovered = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
    assert recovered["status"] == "INVALIDATED"

def test_signal_limit_is_clamped_and_symbol_filter_is_normalized():
    # Invalid symbols are rejected; limit=0 is safely clamped to one item.
    response = client.get("/api/v1/scanner/vpt-x/signals?underlying=nifty&limit=0")
    assert response.status_code == 200
    assert response.json()["count"] <= 1
    assert client.get("/api/v1/scanner/vpt-x/signals?underlying=FINNIFTY").status_code == 422


def test_signal_state_expiry_uses_state_age_not_refresh_time():
    from app.scanner.vpt_x_reversal_routes import _cleanup, _history
    with _lock:
        _history["test:signal"] = {
            "status": "WATCH", "_updated_ms": 50_000, "_state_started_ms": 10_000,
            "underlying": "NIFTY"
        }
        # Refresh occurred recently, but the WATCH state itself is 46s old.
        _cleanup(56_000)
        assert _history["test:signal"]["status"] == "EXPIRED"


def test_invalidated_signal_requires_watch_observation_before_retrigger(monkeypatch):
    import app.scanner.vpt_x_reversal_routes as routes
    clock = [30_000]
    states = [{"price": 101, "trigger": True}, {"price": 98, "trigger": True}, {"price": 101, "trigger": True}, {"price": 102, "trigger": False}, {"price": 101, "trigger": True}]
    monkeypatch.setattr(routes, "_now_ms", lambda: clock[0])
    monkeypatch.setattr(routes, "evaluate_vptx", lambda snapshot, now_ms=None: _stub_eval(states[0]["price"], states[0]["trigger"]))
    assert client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]["status"] == "TRIGGERED"
    for expected in ["INVALIDATED", "WATCH", "WATCH", "TRIGGERED"]:
        states.pop(0)
        clock[0] += 100
        monkeypatch.setattr(routes, "evaluate_vptx", lambda snapshot, now_ms=None: _stub_eval(states[0]["price"], states[0]["trigger"]))
        result = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
        assert result["status"] == expected

def test_expired_signal_requires_fresh_watch_before_retrigger(monkeypatch):
    import app.scanner.vpt_x_reversal_routes as routes
    clock = [100_000]
    monkeypatch.setattr(routes, "_now_ms", lambda: clock[0])
    monkeypatch.setattr(routes, "evaluate_vptx", lambda snapshot, now_ms=None: _stub_eval(101, True))
    first = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
    assert first["status"] == "TRIGGERED"

    # Expire the prior state (45s state TTL) before the next evaluate call.
    clock[0] += 46_000
    expired_rearm = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
    assert expired_rearm["status"] == "WATCH"
    assert expired_rearm["signal_id"] != first["signal_id"]

    clock[0] += 100
    fresh_trigger = client.post("/api/v1/scanner/vpt-x/evaluate", json={"underlying": "NIFTY"}).json()["candidates"][0]
    assert fresh_trigger["status"] == "TRIGGERED"
    assert fresh_trigger["signal_id"] != expired_rearm["signal_id"]
