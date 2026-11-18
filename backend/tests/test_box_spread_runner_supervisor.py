import threading
import time

from app.market_data.box_spread_runner_supervisor import BoxSpreadRunnerSupervisor


class FakeRunner:
    def __init__(self, targets, ready=False):
        self.targets = tuple(targets)
        self.ready = ready
        self.stop_event = threading.Event()
        self.activated = 0
        self.deactivated = 0

    def run_forever(self):
        self.stop_event.wait()

    def stop(self):
        self.stop_event.set()

    def staged_readiness(self):
        return {"ready": self.ready, "state": "READY" if self.ready else "WAITING"}

    def activate_publishing(self):
        self.activated += 1

    def deactivate_publishing(self):
        self.deactivated += 1


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_supervisor_transfers_only_after_candidate_is_ready():
    old = FakeRunner([type("Target", (), {"underlying": "NIFTY"})()])
    old_thread = threading.Thread(target=old.run_forever, daemon=True)
    old_thread.start()
    candidates = []

    def factory(targets):
        candidate = FakeRunner(targets, ready=True)
        candidates.append(candidate)
        return candidate

    supervisor = BoxSpreadRunnerSupervisor(
        active_runner=old,
        active_thread=old_thread,
        runner_factory=factory,
        readiness_poll_seconds=0.01,
    )
    result = supervisor.request_handoff([type("Target", (), {"underlying": "SENSEX"})()])
    assert result["state"] == "STAGING"
    assert wait_for(lambda: supervisor.snapshot()["handoff"]["state"] == "ACTIVE")
    candidate = candidates[0]
    assert supervisor.snapshot()["active_symbols"] == ["SENSEX"]
    assert candidate.activated == 1
    assert old.stop_event.is_set()
    assert candidate.stop_event.is_set() is False
    supervisor.stop()


def test_supervisor_failed_staging_keeps_old_runner_active():
    old = FakeRunner([type("Target", (), {"underlying": "NIFTY"})()])
    old_thread = threading.Thread(target=old.run_forever, daemon=True)
    old_thread.start()
    candidates = []

    def factory(targets):
        candidate = FakeRunner(targets, ready=False)
        candidates.append(candidate)
        return candidate

    supervisor = BoxSpreadRunnerSupervisor(
        active_runner=old,
        active_thread=old_thread,
        runner_factory=factory,
        readiness_timeout_seconds=0.08,
        readiness_poll_seconds=0.01,
    )
    supervisor.request_handoff([type("Target", (), {"underlying": "BANKNIFTY"})()])
    assert wait_for(lambda: supervisor.snapshot()["handoff"]["state"] == "FAILED")
    assert supervisor.snapshot()["active_symbols"] == ["NIFTY"]
    assert old.stop_event.is_set() is False
    assert candidates[0].stop_event.is_set() is True
    assert candidates[0].activated == 0
    supervisor.stop()

def test_supervisor_invokes_selection_rollback_after_async_staging_failure():
    old = FakeRunner([type("Target", (), {"underlying": "NIFTY"})()])
    old_thread = threading.Thread(target=old.run_forever, daemon=True)
    old_thread.start()
    rolled_back = []

    def factory(targets):
        return FakeRunner(targets, ready=False)

    supervisor = BoxSpreadRunnerSupervisor(
        active_runner=old,
        active_thread=old_thread,
        runner_factory=factory,
        readiness_timeout_seconds=0.08,
        readiness_poll_seconds=0.01,
    )
    supervisor.request_handoff(
        [type("Target", (), {"underlying": "SENSEX"})()],
        on_failed=lambda: rolled_back.append(True),
    )
    assert wait_for(lambda: supervisor.snapshot()["handoff"]["state"] == "FAILED")
    assert rolled_back == [True]
    assert supervisor.snapshot()["active_symbols"] == ["NIFTY"]
    assert old.stop_event.is_set() is False
    supervisor.stop()



def test_supervisor_rejects_parallel_handoff_requests():
    old = FakeRunner([type("Target", (), {"underlying": "NIFTY"})()])
    old_thread = threading.Thread(target=old.run_forever, daemon=True)
    old_thread.start()
    candidates = []

    def factory(targets):
        candidate = FakeRunner(targets, ready=False)
        candidates.append(candidate)
        return candidate

    supervisor = BoxSpreadRunnerSupervisor(
        active_runner=old,
        active_thread=old_thread,
        runner_factory=factory,
        readiness_timeout_seconds=0.2,
        readiness_poll_seconds=0.01,
    )
    first = supervisor.request_handoff([type("Target", (), {"underlying": "SENSEX"})()])
    second = supervisor.request_handoff([type("Target", (), {"underlying": "BANKEX"})()])
    assert first["state"] == "STAGING"
    assert second["state"] == "HANDOFF_ALREADY_RUNNING"
    assert len(candidates) == 1
    supervisor.stop()



def test_supervisor_restores_old_publisher_when_candidate_activation_raises():
    class ActivationFailureRunner(FakeRunner):
        def activate_publishing(self):
            self.activated += 1
            raise RuntimeError("pipeline activation failed")

    old = FakeRunner([type("Target", (), {"underlying": "NIFTY"})()])
    old_thread = threading.Thread(target=old.run_forever, daemon=True)
    old_thread.start()
    candidates = []

    def factory(targets):
        candidate = ActivationFailureRunner(targets, ready=True)
        candidates.append(candidate)
        return candidate

    supervisor = BoxSpreadRunnerSupervisor(
        active_runner=old,
        active_thread=old_thread,
        runner_factory=factory,
        readiness_poll_seconds=0.01,
    )
    supervisor.request_handoff([type("Target", (), {"underlying": "SENSEX"})()])
    assert wait_for(lambda: supervisor.snapshot()["handoff"]["state"] == "FAILED")
    assert supervisor.snapshot()["active_symbols"] == ["NIFTY"]
    assert old.activated == 1
    assert old.stop_event.is_set() is False
    assert candidates[0].stop_event.is_set() is True
    supervisor.stop()
