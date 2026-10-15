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

def test_empty_completed_scan_publishes_empty_results_but_incomplete_scan_does_not():
    class DiagnosticScanner:
        def __init__(self):
            self.evaluations = 0
            self.complete = False

        def diagnostics_snapshot(self):
            return {"counters": {"scan_evaluations": self.evaluations}}

        def observe(self, _payload):
            if self.complete:
                self.evaluations += 1
            return ()

    scanner = DiagnosticScanner()
    published = []
    pipeline = LiveSyntheticScanPipeline(
        scanner=scanner,
        on_results=published.append,
    )

    # An incomplete/missing-leg observation must not clear a valid dashboard.
    assert pipeline.observe({"leg": "CE"}) == ()
    assert published == []

    # A completed calculation with no opportunity must clear stale results.
    scanner.complete = True
    assert pipeline.observe({"leg": "FUTURE"}) == ()
    assert published == [()]


def test_nonempty_results_are_published_and_empty_completed_results_are_not_persisted_as_alerts():
    class DiagnosticScanner:
        def __init__(self, results):
            self.results = results
            self.evaluations = 0

        def diagnostics_snapshot(self):
            return {"counters": {"scan_evaluations": self.evaluations}}

        def observe(self, _payload):
            self.evaluations += 1
            return self.results

    result = SimpleNamespace()
    published = []
    pipeline = LiveSyntheticScanPipeline(
        scanner=DiagnosticScanner((result,)),
        on_results=published.append,
    )
    assert pipeline.observe({"leg": "complete"}) == (result,)
    assert published == [(result,)]
