from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def _client_and_headers():
    client = TestClient(app)
    email = f"paper-position-{uuid4().hex}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "full_name": "Paper Test"},
    )
    assert response.status_code == 201
    return client, {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_paper_position_lifecycle():
    client, headers = _client_and_headers()

    entry_response = client.post(
        "/api/v1/execution/paper/entry",
        headers=headers,
        json={"price": 100.0, "quantity": 2.0, "stop_loss_pct": 0.02, "target_pct": 0.04},
    )
    assert entry_response.status_code == 200
    entry = entry_response.json()
    assert entry["status"] == "success"
    assert entry["position"]["entry_price"] == 100.0
    assert entry["position"]["stop_loss"] == 98.0
    assert entry["position"]["target"] == 104.0

    active_response = client.get("/api/v1/execution/paper/position", headers=headers)
    assert active_response.status_code == 200
    active = active_response.json()
    assert active["status"] == "active"
    assert active["position"]["quantity"] == 2.0

    exit_response = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"price": 103.5},
    )
    assert exit_response.status_code == 200
    exit_result = exit_response.json()
    assert exit_result["status"] == "closed"
    assert exit_result["entry_price"] == 100.0
    assert exit_result["exit_price"] == 103.5
    assert exit_result["quantity"] == 2.0
    assert exit_result["pnl"] == 7.0
    assert exit_result["order"]["transaction_type"] == "SELL"

    flat_response = client.get("/api/v1/execution/paper/position", headers=headers)
    assert flat_response.status_code == 200
    assert flat_response.json() == {"status": "flat", "position": None}


def test_paper_exit_without_position_is_flat():
    client, headers = _client_and_headers()
    response = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"price": 101.0},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "flat"
    assert result["position"] is None
    assert result["pnl"] == 0.0
