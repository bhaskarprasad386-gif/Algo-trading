from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.core.security import ALGORITHM
from app.main import app


def _auth_headers():
    token = jwt.encode({"sub": "1"}, settings.SECRET_KEY, algorithm=ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


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


def test_paper_entry_calculates_fill_based_levels_for_authenticated_user():
    client = TestClient(app)
    response = client.post(
        "/api/v1/execution/paper/entry",
        headers=_auth_headers(),
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


def test_paper_entry_rejects_non_positive_values_for_authenticated_user():
    client = TestClient(app)
    response = client.post(
        "/api/v1/execution/paper/entry",
        headers=_auth_headers(),
        json={"price": 0, "quantity": 1},
    )
    assert response.status_code == 422
