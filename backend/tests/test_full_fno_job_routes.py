from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.scanner import routes

client = TestClient(app)


def _db():
    yield object()


def test_full_fno_start_api_contract(monkeypatch):
    captured = {}
    monkeypatch.setattr(routes, "create_full_fno_job", lambda **kwargs: (captured.update(kwargs) or SimpleNamespace(job_id="api-job")))
    app.dependency_overrides[get_db] = _db
    try:
        response = client.post("/api/v1/scanner/cash-future/backtest/full/jobs", params={"days": 365, "min_entry_gap": 5, "future_selection": "NEAR"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "universe": "FULL_FNO_STOCK", "future_selection": "NEAR", "job": "api-job"}
    assert captured["days"] == 365 and captured["min_entry_gap"] == 5.0 and captured["future_selection"] == "NEAR"


def test_full_fno_start_api_rejects_invalid_selection():
    response = client.post("/api/v1/scanner/cash-future/backtest/full/jobs", params={"future_selection": "INVALID"})
    assert response.status_code == 422


def test_full_fno_status_api_contract(monkeypatch):
    job = SimpleNamespace(job_id="status-job", status="running", symbol="__FULL_FNO__", contract_month="BOTH", requested_days=365, progress_pct=42.5, symbols_processed=17, symbols_total=100, message="Running", result_json=None, created_at=None, updated_at=None)
    monkeypatch.setattr(routes, "get_job", lambda db, job_id: job if job_id == job.job_id else None)
    monkeypatch.setattr(routes, "result_chunk_count", lambda db, job_id: 17)
    app.dependency_overrides[get_db] = _db
    try:
        response = client.get(f"/api/v1/scanner/cash-future/backtest/jobs/{job.job_id}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()["job"]
    assert payload["status"] == "running"
    assert payload["progress_pct"] == 42.5
    assert payload["symbols_processed"] == 17 and payload["symbols_total"] == 100
    assert payload["result_chunks"] == 17 and payload["result"] is None


def test_full_fno_results_api_keyset_contract(monkeypatch):
    job = SimpleNamespace(job_id="results-job")
    chunks = [SimpleNamespace(sequence=5, symbol="RELIANCE", result_json='{"net_profit": 100.0}', created_at=None), SimpleNamespace(sequence=6, symbol="TCS", result_json='{"net_profit": 200.0}', created_at=None)]
    monkeypatch.setattr(routes, "get_job", lambda db, job_id: job if job_id == job.job_id else None)
    monkeypatch.setattr(routes, "get_result_chunks", lambda db, job_id, offset, limit, after_sequence: chunks)
    monkeypatch.setattr(routes, "result_chunk_count", lambda db, job_id: 9)
    app.dependency_overrides[get_db] = _db
    try:
        response = client.get(f"/api/v1/scanner/cash-future/backtest/jobs/{job.job_id}/results", params={"after_sequence": 4, "limit": 200})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["after_sequence"] == 4 and payload["next_after_sequence"] == 6 and payload["total"] == 9
    assert [item["sequence"] for item in payload["data"]] == [5, 6]
    assert "ledger" not in payload["data"][0]["result"]


def test_full_fno_results_api_rejects_limit_above_200():
    response = client.get("/api/v1/scanner/cash-future/backtest/jobs/any/results", params={"limit": 201})
    assert response.status_code == 422


def test_full_fno_cancel_api_contract(monkeypatch):
    monkeypatch.setattr(routes, "cancel_job", lambda db, job_id: job_id == "cancel-job")
    app.dependency_overrides[get_db] = _db
    try:
        response = client.delete("/api/v1/scanner/cash-future/backtest/jobs/cancel-job")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "success", "job_id": "cancel-job", "job_status": "cancelled"}


def test_full_fno_cancel_api_missing_job_is_404(monkeypatch):
    monkeypatch.setattr(routes, "cancel_job", lambda db, job_id: False)
    app.dependency_overrides[get_db] = _db
    try:
        response = client.delete("/api/v1/scanner/cash-future/backtest/jobs/missing")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


def test_full_fno_purge_api_terminal_contract(monkeypatch):
    job = SimpleNamespace(job_id="purge-job", status="completed")
    monkeypatch.setattr(routes, "get_job", lambda db, job_id: job if job_id == job.job_id else None)
    monkeypatch.setattr(routes, "delete_result_chunks_batched", lambda db, job_id: 405)
    app.dependency_overrides[get_db] = _db
    try:
        response = client.delete(f"/api/v1/scanner/cash-future/backtest/jobs/{job.job_id}/results")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["deleted_chunks"] == 405


def test_full_fno_purge_api_rejects_active_job(monkeypatch):
    job = SimpleNamespace(job_id="active-job", status="running")
    monkeypatch.setattr(routes, "get_job", lambda db, job_id: job if job_id == job.job_id else None)
    app.dependency_overrides[get_db] = _db
    try:
        response = client.delete(f"/api/v1/scanner/cash-future/backtest/jobs/{job.job_id}/results")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
