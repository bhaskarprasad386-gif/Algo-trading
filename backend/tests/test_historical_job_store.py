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
