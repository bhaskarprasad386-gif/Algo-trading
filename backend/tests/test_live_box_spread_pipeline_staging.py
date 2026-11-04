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
