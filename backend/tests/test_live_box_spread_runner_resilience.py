from app.market_data.live_box_spread_runner import LiveBoxSpreadRunner


def test_box_runner_retries_transient_session_failure(monkeypatch):
    runner = LiveBoxSpreadRunner(
        "memory.db",
        (),
        allowed_stock_symbols=(),
    )
    calls = []

    def run_session():
        calls.append("session")
        if len(calls) == 1:
            raise RuntimeError("transient")
        runner.stop_event.set()

    monkeypatch.setattr(runner, "_run_session", run_session)
    monkeypatch.setattr(runner.stop_event, "wait", lambda _seconds: False)

    runner.run_forever()

    assert calls == ["session", "session"]
