import asyncio

import pytest

from app import main


def test_iv_recovery_reads_saved_control_state_off_event_loop(monkeypatch):
    calls = []

    async def fake_run_blocking_daemon(function, *, thread_name):
        calls.append((function, thread_name))
        return False

    async def cancel_on_next_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "run_blocking_daemon", fake_run_blocking_daemon)
    monkeypatch.setattr(main.asyncio, "sleep", cancel_on_next_sleep)

    async def run_once_then_cancel():
        with pytest.raises(asyncio.CancelledError):
            await main._iv_scanner_recovery_loop()

    asyncio.run(run_once_then_cancel())

    assert calls == [
        (main._get_iv_scanner_enabled, "iv-scanner-control-state-read")
    ]
