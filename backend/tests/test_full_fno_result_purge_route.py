from types import SimpleNamespace

import pytest

from app.scanner import routes


class _DummyDB:
    pass


def test_full_fno_result_purge_rejects_active_job(monkeypatch):
    job = SimpleNamespace(status="running")
    monkeypatch.setattr(routes, "get_job", lambda db, job_id: job)

    with pytest.raises(routes.HTTPException) as exc:
        routes.purge_cash_future_backtest_job_results("active-job", _DummyDB())

    assert exc.value.status_code == 409
    assert "terminal job" in exc.value.detail


def test_full_fno_result_purge_deletes_terminal_chunks_in_batches(monkeypatch):
    job = SimpleNamespace(status="completed")
    calls = []
    monkeypatch.setattr(routes, "get_job", lambda db, job_id: job)
    monkeypatch.setattr(
        routes,
        "delete_result_chunks_batched",
        lambda db, job_id: calls.append((db, job_id)) or 7,
    )

    result = routes.purge_cash_future_backtest_job_results("done-job", _DummyDB())

    assert result == {
        "status": "success",
        "job_id": "done-job",
        "job_status": "completed",
        "deleted_chunks": 7,
    }
    assert len(calls) == 1
    assert calls[0][1] == "done-job"


def test_full_fno_result_purge_allows_cancelled_job(monkeypatch):
    job = SimpleNamespace(status="cancelled")
    monkeypatch.setattr(routes, "get_job", lambda db, job_id: job)
    monkeypatch.setattr(routes, "delete_result_chunks_batched", lambda db, job_id: 3)

    result = routes.purge_cash_future_backtest_job_results("cancelled-job", _DummyDB())

    assert result["job_status"] == "cancelled"
    assert result["deleted_chunks"] == 3
