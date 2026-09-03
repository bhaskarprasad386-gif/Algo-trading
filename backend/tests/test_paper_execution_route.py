from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def _client_and_headers():
    client = TestClient(app)
    email = f"paper-execution-{uuid4().hex}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "full_name": "Paper Test"},
    )
    assert response.status_code == 201
    return client, {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_paper_entry_route_registered():
    paths = app.openapi().get("paths", {})
    assert "/api/v1/execution/paper/entry" in paths


def test_paper_entry_requires_authentication():
    client = TestClient(app)
    response = client.post(
        "/api/v1/execution/paper/entry",
        json={"price": 100.0, "quantity": 2, "stop_loss_pct": 0.05, "target_pct": 0.10},
    )
    assert response.status_code == 401


def test_paper_entry_persists_position_and_order_and_updates_balance():
    client, headers = _client_and_headers()
    before = client.get("/api/v1/auth/me", headers=headers)
    assert before.status_code == 200
    starting_balance = before.json()["account"]["virtual_balance"]

    response = client.post(
        "/api/v1/execution/paper/entry",
        headers=headers,
        json={"price": 100.0, "quantity": 2, "stop_loss_pct": 0.05, "target_pct": 0.10},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["mode"] == "paper"
    assert data["fill"] == {"price": 100.0, "quantity": 2.0}
    assert data["entry_price"] == 100.0
    assert data["stop_loss"] == 95.0
    assert data["target"] == 110.0
    assert data["virtual_balance"] == starting_balance - 200.0
    assert data["order"]["transaction_type"] == "BUY"

    position_response = client.get("/api/v1/execution/paper/position", headers=headers)
    assert position_response.status_code == 200
    position = position_response.json()["position"]
    assert position["symbol"] == "PAPER"
    assert position["quantity"] == 2.0
    assert position["entry_price"] == 100.0

    orders_response = client.get("/api/v1/execution/paper/orders", headers=headers)
    assert orders_response.status_code == 200
    orders = orders_response.json()["orders"]
    assert orders[-1]["transaction_type"] == "BUY"
    assert orders[-1]["price"] == 100.0
    assert orders[-1]["quantity"] == 2.0

    exit_response = client.post(
        "/api/v1/execution/paper/exit",
        headers=headers,
        json={"price": 105.0},
    )
    assert exit_response.status_code == 200
    exit_data = exit_response.json()
    assert exit_data["pnl"] == 10.0
    assert exit_data["realized_pnl"] == 10.0
    assert exit_data["virtual_balance"] == starting_balance + 10.0
    assert exit_data["order"]["transaction_type"] == "SELL"

    flat_response = client.get("/api/v1/execution/paper/position", headers=headers)
    assert flat_response.status_code == 200
    assert flat_response.json() == {"status": "flat", "position": None}

    orders_after_exit = client.get("/api/v1/execution/paper/orders", headers=headers)
    assert orders_after_exit.status_code == 200
    assert orders_after_exit.json()["orders"][-1]["transaction_type"] == "SELL"


def test_paper_entry_rejects_non_positive_values_for_authenticated_user():
    client, headers = _client_and_headers()
    response = client.post(
        "/api/v1/execution/paper/entry",
        headers=headers,
        json={"price": 0, "quantity": 1},
    )
    assert response.status_code == 422
