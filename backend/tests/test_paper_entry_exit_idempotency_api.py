from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def _client_and_headers():
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"paper-entry-exit-idempotency-{uuid4().hex}@example.com",
            "password": "TestPass123!",
            "full_name": "Paper Entry Exit Idempotency Test",
        },
    )
    assert response.status_code == 201
    return client, {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_paper_entry_retry_replays_exact_response_and_does_not_reexecute():
    client, headers = _client_and_headers()
    starting_balance = client.get("/api/v1/auth/me", headers=headers).json()["account"]["virtual_balance"]
    request_headers = {**headers, "Idempotency-Key": "entry-retry-001"}
    payload = {"symbol": "ENTRY_IDEMPOTENT", "price": 100.0, "quantity": 2}

    first = client.post("/api/v1/execution/paper/entry", headers=request_headers, json=payload)
    assert first.status_code == 200

    retry = client.post("/api/v1/execution/paper/entry", headers=request_headers, json=payload)
    assert retry.status_code == 200
    assert retry.json() == first.json()

    orders = client.get("/api/v1/execution/paper/orders", headers=headers).json()["orders"]
    matching = [order for order in orders if order["symbol"] == "ENTRY_IDEMPOTENT"]
    assert len(matching) == 1
    assert retry.json()["virtual_balance"] == starting_balance - 200.0


def test_paper_exit_retry_replays_exact_response_and_does_not_reexecute():
    client, headers = _client_and_headers()
    entry_headers = {**headers, "Idempotency-Key": "exit-entry-001"}
    entry = client.post(
        "/api/v1/execution/paper/entry",
        headers=entry_headers,
        json={"symbol": "EXIT_IDEMPOTENT", "price": 100.0, "quantity": 2},
    )
    assert entry.status_code == 200

    exit_headers = {**headers, "Idempotency-Key": "exit-retry-001"}
    payload = {"symbol": "EXIT_IDEMPOTENT", "price": 110.0}
    first = client.post("/api/v1/execution/paper/exit", headers=exit_headers, json=payload)
    assert first.status_code == 200

    retry = client.post("/api/v1/execution/paper/exit", headers=exit_headers, json=payload)
    assert retry.status_code == 200
    assert retry.json() == first.json()

    orders = client.get("/api/v1/execution/paper/orders", headers=headers).json()["orders"]
    matching = [order for order in orders if order["symbol"] == "EXIT_IDEMPOTENT"]
    assert len(matching) == 2
