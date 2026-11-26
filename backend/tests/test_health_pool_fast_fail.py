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


def test_health_endpoint_classifies_sqlite_lock_contention_separately(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(main, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    monkeypatch.setattr(main, "database_pool_snapshot", lambda: {
        "pool_class": "QueuePool", "checkedout": 2, "capacity": 15, "saturated": False,
        "long_checkout_count": 0,
    })
    monkeypatch.setattr(
        main, "check_database",
        lambda: (_ for _ in ()).throw(RuntimeError("database is locked")),
    )

    response = TestClient(main.app).get("/health")

    assert response.status_code == 503
    assert response.json()["database"] == "Contended"
    assert response.json()["database_error_class"] == "SQLITE_CONTENTION"
    assert response.json()["database_pool"]["long_checkout_count"] == 0


def test_health_probe_timeout_is_bounded_and_single_flight(monkeypatch):
    import threading
    import time

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    monkeypatch.setattr(main, "database_pool_snapshot", lambda: {
        "pool_class": "QueuePool",
        "checkedout": 14,
        "capacity": 15,
        "saturated": False,
    })

    def blocked_database_check():
        calls.append("select")
        started.set()
        try:
            release.wait(timeout=5)
        finally:
            finished.set()
        return True

    monkeypatch.setattr(main, "check_database", blocked_database_check)
    client = TestClient(main.app)
    try:
        started_at = time.monotonic()
        first = client.get("/health")
        elapsed = time.monotonic() - started_at

        assert started.is_set()
        assert first.status_code == 503
        assert first.json()["database_error_class"] == "DATABASE_PROBE_TIMEOUT"
        assert first.json()["database"] == "Probe timed out"
        assert elapsed < 1.5

        # The timed-out worker is still blocked, so a second request must fail
        # immediately instead of creating another thread waiting for a checkout.
        second_started_at = time.monotonic()
        second = client.get("/health")
        second_elapsed = time.monotonic() - second_started_at

        assert second.status_code == 503
        assert second.json()["database_error_class"] == "DATABASE_PROBE_BUSY"
        assert second.json()["database"] == "Probe busy"
        assert second_elapsed < 0.5
        assert calls == ["select"]
    finally:
        release.set()
        assert finished.wait(timeout=2)
