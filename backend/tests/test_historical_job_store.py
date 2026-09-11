from app.backtesting.historical_job_store import HistoricalJobStore


def test_job_store_persists_chunk_progress_and_resume_indices(tmp_path):
    path = str(tmp_path / "jobs.db")
    store = HistoricalJobStore(path)
    fingerprint = store.fingerprint((
        {"instrument": "SBIN", "timeframe": "1m", "start_ns": 0, "end_ns": 10},
        {"instrument": "INFY", "timeframe": "1m", "start_ns": 0, "end_ns": 10},
        {"instrument": "TCS", "timeframe": "1m", "start_ns": 0, "end_ns": 10},
    ))
    store.create(job_id="job-1", run_id="run-1", plan_fingerprint=fingerprint, total_chunks=3)

    store.start_chunk("job-1", 0)
    store.complete_chunk("job-1", 0)
    store.start_chunk("job-1", 1)
    store.fail_chunk("job-1", 1, "provider timeout")
    store.finish("job-1")
    store.close()

    resumed = HistoricalJobStore(path)
    job = resumed.get("job-1")
    assert job.state == "progress"
    assert job.completed_chunks == 1
    assert job.skipped_chunks == 0
    assert resumed.pending_indices("job-1") == (1, 2)
    assert resumed.chunk_state("job-1", 1) == ("recoverable", 1, "provider timeout")


def test_job_fingerprint_is_stable_for_equivalent_requests():
    a = ({"b": 2, "a": 1}, {"instrument": "SBIN", "timeframe": "1m"})
    b = ({"a": 1, "b": 2}, {"timeframe": "1m", "instrument": "SBIN"})
    assert HistoricalJobStore.fingerprint(a) == HistoricalJobStore.fingerprint(b)


def test_job_store_rejects_invalid_chunk_transitions(tmp_path):
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    store.create(job_id="job-1", run_id="run-1", plan_fingerprint="fp", total_chunks=1)

    try:
        store.complete_chunk("job-1", 3)
    except KeyError:
        pass
    else:
        raise AssertionError("expected unknown chunk rejection")

    store.start_chunk("job-1", 0)
    store.complete_chunk("job-1", 0)
    try:
        store.start_chunk("job-1", 0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected completed chunk transition rejection")


def test_finish_does_not_hide_terminal_failure(tmp_path):
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    store.create(job_id="job-1", run_id="run-1", plan_fingerprint="fp", total_chunks=1)
    store.start_chunk("job-1", 0)
    store.fail_chunk("job-1", 0, "permanent provider error", recoverable=False)

    job = store.finish("job-1")
    assert job.state == "failed"
    assert job.failed_chunk == 0
    assert job.error == "permanent provider error"


def test_recover_running_chunks_after_worker_crash(tmp_path):
    path = str(tmp_path / "jobs.db")
    store = HistoricalJobStore(path)
    store.create(job_id="job-1", run_id="run-1", plan_fingerprint="fp", total_chunks=3)
    store.start_chunk("job-1", 0)
    store.start_chunk("job-1", 1)
    assert store.chunk_state("job-1", 0) == ("running", 1, None)
    assert store.chunk_state("job-1", 1) == ("running", 1, None)
    store.close()

    resumed = HistoricalJobStore(path)
    assert resumed.recover_running_chunks("job-1") == (0, 1)
    assert resumed.pending_indices("job-1") == (0, 1, 2)
    assert resumed.chunk_state("job-1", 0) == (
        "recoverable", 1, "recovered after interrupted run"
    )
    assert resumed.chunk_state("job-1", 1) == (
        "recoverable", 1, "recovered after interrupted run"
    )
    assert resumed.get("job-1").state == "recoverable"
    assert resumed.recover_running_chunks("job-1") == ()


def test_recover_running_chunks_never_reopens_completed(tmp_path):
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    store.create(job_id="job-1", run_id="run-1", plan_fingerprint="fp", total_chunks=2)
    store.start_chunk("job-1", 0)
    store.complete_chunk("job-1", 0)
    store.start_chunk("job-1", 1)

    assert store.recover_running_chunks("job-1") == (1,)
    assert store.chunk_state("job-1", 0)[0] == "completed"
    assert store.chunk_state("job-1", 1)[0] == "recoverable"
    assert store.chunk_state("job-1", 1)[1] == 1


def test_cancel_preserves_resumable_work(tmp_path):
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    store.create(job_id="job-1", run_id="run-1", plan_fingerprint="fp", total_chunks=3)
    store.start_chunk("job-1", 0)
    store.complete_chunk("job-1", 0)
    store.start_chunk("job-1", 1)

    cancelled = store.cancel("job-1", reason="operator stop")
    assert cancelled.state == "cancelled"
    assert cancelled.completed_chunks == 1
    assert cancelled.error == "operator stop"
    assert store.pending_indices("job-1") == (2,)
    assert store.chunk_state("job-1", 1)[0] == "running"

    resumed = store.reopen_cancelled("job-1")
    assert resumed.state == "progress"
    assert resumed.completed_chunks == 1
    assert resumed.pending_indices("job-1") == (2,)


def test_cancel_rejects_terminal_jobs(tmp_path):
    store = HistoricalJobStore(str(tmp_path / "jobs.db"))
    store.create(job_id="job-1", run_id="run-1", plan_fingerprint="fp", total_chunks=0)
    store.finish("job-1")
    try:
        store.cancel("job-1")
    except ValueError:
        pass
    else:
        raise AssertionError("expected completed job cancellation rejection")
