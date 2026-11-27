import asyncio
import threading

from app.scanner import box_spread_paper_rule_routes as rules


def test_monitor_diagnostic_persistence_runs_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    observed = {}

    def fake_record(*args, **kwargs):
        observed["thread"] = threading.get_ident()
        observed["args"] = args
        observed["kwargs"] = kwargs

    monkeypatch.setattr(rules, "_record", fake_record)

    async def scenario():
        await rules._record_off_loop(
            7, "paper-account", "MONITOR", "ERROR", "injected"
        )

    asyncio.run(scenario())

    assert observed["thread"] != event_loop_thread
    assert observed["args"] == (
        7, "paper-account", "MONITOR", "ERROR", "injected"
    )
