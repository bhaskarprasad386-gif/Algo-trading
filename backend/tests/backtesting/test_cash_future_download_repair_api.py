from datetime import datetime, timezone
from types import SimpleNamespace

from app.backtesting.cash_future_download_routes import CashFutureDownloadManager


class FakeStatusStore:
    def __init__(self):
        self._job = SimpleNamespace(status="FAILED")
        self.resumed = None

    def job(self, job_id):
        return self._job

    def incomplete_chunks(self, job_id):
        return (SimpleNamespace(sequence=2, missing_timestamps=1),)

    def update_job(self, *args, **kwargs):
        self._job.status = kwargs.get("status", self._job.status)


def test_repair_delegates_to_targeted_resume(monkeypatch):
    store = FakeStatusStore()
    manager = CashFutureDownloadManager(
        data_db=":memory:", contract_db=":memory:", status_store=store
    )

    calls = {}

    def fake_resume(job_id, *, retry_attempts=3):
        calls["job_id"] = job_id
        calls["retry_attempts"] = retry_attempts

    monkeypatch.setattr(manager, "resume", fake_resume)
    manager.repair("job-1", retry_attempts=7)

    assert calls == {"job_id": "job-1", "retry_attempts": 7}


def test_repair_rejects_job_without_gaps():
    store = FakeStatusStore()
    store.incomplete_chunks = lambda job_id: ()
    manager = CashFutureDownloadManager(
        data_db=":memory:", contract_db=":memory:", status_store=store
    )

    try:
        manager.repair("job-1")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("repair must reject jobs without gaps")
