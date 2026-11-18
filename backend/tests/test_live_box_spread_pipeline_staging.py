from app.scanner import live_box_spread_pipeline as module


class FakeScanner:
    def __init__(self, result):
        self.result = result

    def observe(self, payload):
        return (self.result,)


def test_staged_pipeline_warms_without_publishing_or_persisting(monkeypatch):
    result = object()
    monkeypatch.setattr(module, "_qualified_opportunity", lambda item: True)
    published = []
    pipeline = module.LiveBoxSpreadPipeline(
        FakeScanner(result),
        session_factory=None,
        on_results=lambda rows: published.append(rows),
        publish_enabled=False,
    )
    pipeline._publish_results = lambda rows: pipeline.on_results(rows)

    assert pipeline.observe({"tick": 1}) == (result,)
    assert published == []

    pipeline.set_publish_enabled(True)
    assert pipeline.observe({"tick": 2}) == (result,)
    assert published == [(result,)]


def test_staged_pipeline_can_be_deactivated_before_next_observation(monkeypatch):
    result = object()
    monkeypatch.setattr(module, "_qualified_opportunity", lambda item: True)
    published = []
    pipeline = module.LiveBoxSpreadPipeline(
        FakeScanner(result),
        session_factory=None,
        on_results=lambda rows: published.append(rows),
        publish_enabled=True,
    )

    pipeline.set_publish_enabled(False)
    assert pipeline.observe({"tick": 1}) == (result,)
    assert published == []



def test_shared_publisher_gate_rejects_old_owner_after_atomic_transfer(monkeypatch):
    from app.scanner.box_spread_publication_gate import BoxSpreadPublicationGate

    gate = BoxSpreadPublicationGate()
    old_owner = object()
    new_owner = object()
    gate.activate(old_owner)

    old_published = []
    new_published = []
    monkeypatch.setattr(module, "_qualified_opportunity", lambda item: True)

    old_pipeline = module.LiveBoxSpreadPipeline(
        FakeScanner(object()), publish_enabled=True,
        publisher_gate=gate, publisher_owner=old_owner,
    )
    new_pipeline = module.LiveBoxSpreadPipeline(
        FakeScanner(object()), publish_enabled=False,
        publisher_gate=gate, publisher_owner=new_owner,
    )
    old_pipeline._publish_results = lambda rows: old_published.append(rows)
    new_pipeline._publish_results = lambda rows: new_published.append(rows)

    assert old_pipeline.observe({"tick": "before-transfer"})
    assert len(old_published) == 1

    gate.activate(new_owner)
    new_pipeline.set_publish_enabled(True)

    assert old_pipeline.observe({"tick": "after-transfer"})
    assert new_pipeline.observe({"tick": "new-owner"})
    assert len(old_published) == 1
    assert len(new_published) == 1
    assert gate.deactivate(old_owner) is False
    assert gate.is_owner(new_owner) is True


def test_publisher_gate_waits_for_inflight_publish_before_transfer():
    from threading import Event, Thread
    from app.scanner.box_spread_publication_gate import BoxSpreadPublicationGate

    gate = BoxSpreadPublicationGate()
    old_owner = object()
    new_owner = object()
    publish_started = Event()
    allow_publish_finish = Event()
    activation_finished = Event()
    gate.activate(old_owner)

    def publish_old():
        def callback():
            publish_started.set()
            assert allow_publish_finish.wait(2)
        assert gate.publish(old_owner, callback) is True

    def activate_new():
        gate.activate(new_owner)
        activation_finished.set()

    publisher = Thread(target=publish_old)
    publisher.start()
    assert publish_started.wait(2)

    activator = Thread(target=activate_new)
    activator.start()
    assert not activation_finished.wait(0.05)

    allow_publish_finish.set()
    publisher.join(2)
    activator.join(2)
    assert not publisher.is_alive()
    assert not activator.is_alive()
    assert activation_finished.is_set()
    assert gate.is_owner(new_owner)



def test_publisher_gate_claims_only_when_idle():
    from app.scanner.box_spread_publication_gate import BoxSpreadPublicationGate

    gate = BoxSpreadPublicationGate()
    first = object()
    second = object()

    assert gate.claim_if_unowned(first) is True
    assert gate.claim_if_unowned(first) is True
    assert gate.claim_if_unowned(second) is False
    assert gate.is_owner(first) is True
    assert gate.deactivate(first) is True
    assert gate.claim_if_unowned(second) is True
    assert gate.is_owner(second) is True
