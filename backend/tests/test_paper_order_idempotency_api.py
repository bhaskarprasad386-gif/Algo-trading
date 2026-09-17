from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def _client_and_headers():
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"paper-idempotency-{uuid4().hex}@example.com",
            "password": "TestPass123!",
            "full_name": "Paper Idempotency Test",
        },
    )
    assert response.status_code == 201
    return client, {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_paper_order_retry_replays_exact_response_and_does_not_reexecute():
    client, headers = _client_and_headers()
    starting_balance = client.get("/api/v1/auth/me", headers=headers).json()["account"]["virtual_balance"]
    payload = {
        "symbol": "IDEMPOTENT",
        "transaction_type": "BUY",
        "price": 100.0,
        "quantity": 2,
    }
    request_headers = {**headers, "Idempotency-Key": "api-retry-001"}

    first = client.post("/api/v1/execution/paper/order", headers=request_headers, json=payload)
    assert first.status_code == 200

    retry = client.post("/api/v1/execution/paper/order", headers=request_headers, json=payload)
    assert retry.status_code == 200
    assert retry.json() == first.json()

    orders = client.get("/api/v1/execution/paper/orders", headers=headers).json()["orders"]
    matching = [order for order in orders if order["symbol"] == "IDEMPOTENT"]
    assert len(matching) == 1
    assert retry.json()["virtual_balance"] == starting_balance - 200.0


def test_paper_order_retry_with_different_payload_is_rejected():
    client, headers = _client_and_headers()
    request_headers = {**headers, "Idempotency-Key": "api-retry-002"}
    first_payload = {
        "symbol": "IDEMPOTENT",
        "transaction_type": "BUY",
        "price": 100.0,
        "quantity": 1,
    }
    changed_payload = {
        "symbol": "IDEMPOTENT",
        "transaction_type": "BUY",
        "price": 101.0,
        "quantity": 1,
    }

    first = client.post("/api/v1/execution/paper/order", headers=request_headers, json=first_payload)
    assert first.status_code == 200

    changed = client.post("/api/v1/execution/paper/order", headers=request_headers, json=changed_payload)
    assert changed.status_code == 409
    assert "different request" in changed.json()["detail"]
