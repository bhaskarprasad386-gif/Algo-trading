from fastapi.testclient import TestClient

from app import main


def test_health_endpoint_fast_fails_when_pool_is_saturated(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main,
        "database_pool_snapshot",
        lambda: {
            "pool_class": "QueuePool",
            "checkedout": 15,
            "capacity": 15,
            "saturated": True,
        },
    )

    def unexpected_database_check():
        calls.append("select")
        raise AssertionError("saturated health probe must not wait for a DB checkout")

    monkeypatch.setattr(main, "check_database", unexpected_database_check)
    response = TestClient(main.app).get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "Pool saturated"
    assert response.json()["database_pool"]["saturated"] is True
    assert calls == []


def test_health_endpoint_requires_select_one_when_pool_is_not_saturated(monkeypatch):
    calls = []
    snapshot = {
        "pool_class": "QueuePool",
        "checkedout": 3,
        "capacity": 15,
        "saturated": False,
    }
    monkeypatch.setattr(main, "database_pool_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "check_database", lambda: calls.append("select") or True)

    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "Connected"
    assert calls == ["select"]


def test_health_endpoint_does_not_treat_unsupported_pool_as_healthy(monkeypatch):
    calls = []
    snapshot = {
        "pool_class": "StaticPool",
        "checkedout": None,
        "capacity": None,
        "saturated": None,
    }
    monkeypatch.setattr(main, "database_pool_snapshot", lambda: snapshot)

    def failed_select():
        calls.append("select")
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main, "check_database", failed_select)
    response = TestClient(main.app).get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "Disconnected"
    assert calls == ["select"]
