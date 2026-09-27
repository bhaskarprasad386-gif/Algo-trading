from contextlib import contextmanager
from types import SimpleNamespace

from app.scanner.live_synthetic_pipeline import LiveSyntheticScanPipeline


class _Scanner:
    def __init__(self, results):
        self.results = results

    def observe(self, _payload):
        return self.results


class _Alerts:
    def __init__(self):
        self.persist_calls = []
        self.notify_calls = []

    def persist(self, _db, results):
        self.persist_calls.append(tuple(results))
        return 0

    def notify_users(self, _db, results):
        self.notify_calls.append(tuple(results))
        return 0


def _session_factory(log):
    @contextmanager
    def factory():
        log.append("open")
        yield object()
        log.append("close")

    return factory


def test_quiet_pipeline_runs_retention_cleanup_without_results():
    sessions = []
    alerts = _Alerts()
    pipeline = LiveSyntheticScanPipeline(
        scanner=_Scanner(()),
        session_factory=_session_factory(sessions),
        alerts=alerts,
        retention_interval_seconds=60.0,
    )

    assert pipeline.observe({"source_timestamp_ns": 1}) == ()
    assert alerts.persist_calls == [()]
    assert alerts.notify_calls == []
    assert sessions == ["open", "close"]

    # The maintenance interval prevents a DB session on every quiet tick.
    assert pipeline.observe({"source_timestamp_ns": 2}) == ()
    assert alerts.persist_calls == [()]


def test_result_pipeline_persists_and_notifies():
    result = SimpleNamespace()
    sessions = []
    alerts = _Alerts()
    pipeline = LiveSyntheticScanPipeline(
        scanner=_Scanner((result,)),
        session_factory=_session_factory(sessions),
        alerts=alerts,
    )

    assert pipeline.observe({"source_timestamp_ns": 1}) == (result,)
    assert alerts.persist_calls == [(result,)]
    assert alerts.notify_calls == [(result,)]
    assert sessions == ["open", "close"]


def test_pipeline_rejects_non_positive_retention_interval():
    try:
        LiveSyntheticScanPipeline(
            scanner=_Scanner(()),
            retention_interval_seconds=0,
        )
    except ValueError as exc:
        assert "retention_interval_seconds" in str(exc)
    else:
        raise AssertionError("expected retention interval validation")
