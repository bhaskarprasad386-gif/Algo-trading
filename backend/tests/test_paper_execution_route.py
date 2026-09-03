from fastapi.testclient import TestClient

from app.main import app


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


def test_paper_entry_rejects_non_positive_values():
    client = TestClient(app)
    response = client.post(
        "/api/v1/execution/paper/entry",
        json={"price": 0, "quantity": 1},
    )
    assert response.status_code == 422
