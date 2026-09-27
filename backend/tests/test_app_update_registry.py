from fastapi.testclient import TestClient

from app.main import app


def test_android_update_metadata_contract():
    with TestClient(app) as client:
        response = client.get("/api/v1/app/update")
    assert response.status_code == 200
    payload = response.json()
    assert payload["platform"] == "android"
    assert isinstance(payload["version_code"], int)
    assert isinstance(payload["version_name"], str)
    assert "apk_url" in payload
    assert "sha256" in payload
    assert isinstance(payload["mandatory"], bool)


def test_android_strategy_registry_contract():
    with TestClient(app) as client:
        response = client.get("/api/v1/app/strategies")
    assert response.status_code == 200
    strategies = response.json()["strategies"]
    assert strategies
    assert all({"id", "name", "version", "enabled", "screen"} <= set(item) for item in strategies)
