from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

from app.scanner.live_synthetic_pipeline import LiveSyntheticScanPipeline


class _Scanner:
    def __init__(self, results):
        self.results = results

    def observe(self, _payload):
        return self.results


class _Alerts:
    def __init__(self, notify_event=None):
        self.persist_calls = []
        self.notify_calls = []
        self.notify_event = notify_event

    def persist(self, _db, results):
        self.persist_calls.append(tuple(results))
        return 0

    def notify_users(self, _db, results):
        if self.notify_event is not None:
            self.notify_event.wait(timeout=2)
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
    try:
        assert pipeline.observe({"source_timestamp_ns": 1}) == ()
        assert pipeline.flush(timeout=2)
        assert alerts.persist_calls == [()]
        assert alerts.notify_calls == []
        assert sessions == ["open", "close"]
        assert pipeline.observe({"source_timestamp_ns": 2}) == ()
        assert pipeline.flush(timeout=0.05)
        assert alerts.persist_calls == [()]
    finally:
        assert pipeline.close(timeout=2)


def test_result_pipeline_persists_and_notifies_off_callback_thread():
    result = SimpleNamespace()
    sessions = []
    alerts = _Alerts()
    pipeline = LiveSyntheticScanPipeline(
        scanner=_Scanner((result,)),
        session_factory=_session_factory(sessions),
        alerts=alerts,
    )
    try:
        assert pipeline.observe({"source_timestamp_ns": 1}) == (result,)
        assert alerts.persist_calls == []
        assert pipeline.flush(timeout=2)
        assert alerts.persist_calls == [(result,)]
        assert alerts.notify_calls == [(result,)]
        assert sessions == ["open", "close"]
    finally:
        assert pipeline.close(timeout=2)


def test_slow_notification_does_not_block_market_data_callback():
    release = Event()
    alerts = _Alerts(notify_event=release)
    pipeline = LiveSyntheticScanPipeline(
        scanner=_Scanner((SimpleNamespace(),)),
        session_factory=_session_factory([]),
        alerts=alerts,
    )
    try:
        assert pipeline.observe({"tick": 1})
        # The worker is held inside notify_users, but another observation returns.
        assert pipeline.observe({"tick": 2})
        snapshot = pipeline.diagnostics_snapshot()
        assert snapshot["alert_worker_alive"]
        assert snapshot["counters"]["alert_jobs_enqueued"] == 2
    finally:
        release.set()
        assert pipeline.close(timeout=2)


def test_alert_queue_overflow_is_counted_not_blocking_market_data():
    release = Event()
    alerts = _Alerts(notify_event=release)
    pipeline = LiveSyntheticScanPipeline(
        scanner=_Scanner((SimpleNamespace(),)),
        session_factory=_session_factory([]),
        alerts=alerts,
        alert_queue_size=1,
    )
    try:
        assert pipeline.observe({"tick": 1})
        # Wait until the first job occupies the worker, then fill the one-slot queue.
        from time import monotonic, sleep
        deadline = monotonic() + 1
        while not alerts.persist_calls and monotonic() < deadline:
            sleep(0.005)
        assert pipeline.observe({"tick": 2})
        assert pipeline.observe({"tick": 3})
        assert pipeline.diagnostics_snapshot()["counters"]["alert_jobs_dropped"] >= 1
    finally:
        release.set()
        assert pipeline.close(timeout=2)


def test_pipeline_rejects_non_positive_retention_interval_and_queue_size():
    for kwargs, expected in (
        ({"retention_interval_seconds": 0}, "retention_interval_seconds"),
        ({"alert_queue_size": 0}, "alert_queue_size"),
    ):
        try:
            LiveSyntheticScanPipeline(scanner=_Scanner(()), **kwargs)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"expected {expected} validation")


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
    pipeline = LiveSyntheticScanPipeline(scanner=scanner, on_results=published.append)
    assert pipeline.observe({"leg": "CE"}) == ()
    assert published == []
    scanner.complete = True
    assert pipeline.observe({"leg": "FUTURE"}) == ()
    assert published == [()]


def test_concurrent_incomplete_tick_cannot_publish_another_ticks_empty_scan():
    from threading import Event, Thread

    entered_incomplete = Event()
    release_incomplete = Event()

    class ConcurrentScanner:
        def __init__(self):
            self.evaluations = 0

        def diagnostics_snapshot(self):
            return {"counters": {"scan_evaluations": self.evaluations}}

        def observe(self, payload):
            if payload["kind"] == "incomplete":
                entered_incomplete.set()
                assert release_incomplete.wait(timeout=2)
                return ()
            self.evaluations += 1
            return ()

    scanner = ConcurrentScanner()
    published = []
    pipeline = LiveSyntheticScanPipeline(scanner=scanner, on_results=published.append)
    first = Thread(target=lambda: pipeline.observe({"kind": "incomplete"}))
    second = Thread(target=lambda: pipeline.observe({"kind": "complete"}))
    first.start()
    assert entered_incomplete.wait(timeout=2)
    second.start()
    release_incomplete.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert published == [()]


def test_nonempty_results_are_published_without_database_configuration():
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
