from fastapi.testclient import TestClient
from app.scanner.vpt_x_reversal_routes import router, _history, _lock
import pytest

@pytest.fixture(autouse=True)
def clear_signal_history():
    with _lock:
        _history.clear()
    yield
    with _lock:
        _history.clear()

# Exercise route contract with a local test app to avoid importing the full trading app.
from fastapi import FastAPI
app=FastAPI()
app.include_router(router)
client=TestClient(app)

def _ticks(prices, start=2_000_000):
    return [{"timestamp_ms":start+i*100,"price":p,"buy_volume":35,"sell_volume":18,
             "bid_qty":100,"ask_qty":45,"bid":p-.05,"ask":p+.05} for i,p in enumerate(prices)]

def test_status_is_isolated_and_off():
    body=client.get("/api/v1/scanner/vpt-x/status").json()
    assert body["feed_connected"] is False and body["scanner_active"] is False
    assert body["order_routing_enabled"] is False

def test_invalid_underlying_rejected():
    assert client.post("/api/v1/scanner/vpt-x/evaluate",json={"underlying":"FINNIFTY"}).status_code==422

def test_waiting_data_does_not_create_history():
    response=client.post("/api/v1/scanner/vpt-x/evaluate",json={"underlying":"NIFTY","ticks":_ticks([100,101,102])}).json()
    assert response["status"]=="WAITING_DATA"
    assert client.get("/api/v1/scanner/vpt-x/signals").json()["count"]==0

def test_evaluate_contract_and_bounded_signal_history():
    response=client.post("/api/v1/scanner/vpt-x/evaluate",json={"underlying":"BANKNIFTY","ticks":_ticks([100+i*.1 for i in range(30)])}).json()
    assert response["status"]=="EVALUATED" and len(response["candidates"])==6
    assert {x["pattern"] for x in response["candidates"]}=={"SWING_EXHAUSTION","COMPRESSION_REVERSAL","V_REVERSAL"}
    assert {x["direction"] for x in response["candidates"]}=={"BUY","SELL"}
    assert all(x["scanner_only"] and x["order_routing_enabled"] is False for x in response["candidates"])
    assert client.get("/api/v1/scanner/vpt-x/signals?underlying=BANKNIFTY").json()["count"]==6
\n\ndef test_idle_signal_transitions_to_expired():\n    from app.scanner.vpt_x_reversal_routes import _cleanup, _history\n    with _lock:\n        _history["test:signal"]={"status":"WATCH","_updated_ms":1,"underlying":"NIFTY"}\n        _cleanup(100_000)\n        assert _history["test:signal"]["status"]=="EXPIRED"\n