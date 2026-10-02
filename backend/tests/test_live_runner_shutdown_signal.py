import threading

from app import main


def test_signal_live_runner_shutdown_sets_runner_interrupt_events(monkeypatch):
    cash = type("CashRunner", (), {})()
    cash.stop_event = threading.Event()

    calendar = type("CalendarRunner", (), {})()
    calendar.stop_event = threading.Event()

    synthetic = type("SyntheticRunner", (), {})()
    synthetic._stop_requested = threading.Event()

    box = type("BoxRunner", (), {})()
    box.stop_event = threading.Event()

    monkeypatch.setattr(main, "live_cash_future_runner", cash)
    monkeypatch.setattr(main, "live_calendar_spread_runner", calendar)
    monkeypatch.setattr(main, "live_synthetic_runner", synthetic)
    monkeypatch.setattr(main, "live_box_spread_runner", box)

    main._signal_live_runner_shutdown()

    assert cash.stop_event.is_set()
    assert calendar.stop_event.is_set()
    assert synthetic._stop_requested.is_set()
    assert box.stop_event.is_set()


def test_signal_live_runner_shutdown_does_not_call_blocking_stop(monkeypatch):
    calls = []

    class Runner:
        def __init__(self):
            self.stop_event = threading.Event()

        def stop(self):
            calls.append("stop")
            raise AssertionError("shutdown signal must not perform blocking cleanup")

    runner = Runner()
    monkeypatch.setattr(main, "live_cash_future_runner", runner)
    monkeypatch.setattr(main, "live_calendar_spread_runner", None)
    monkeypatch.setattr(main, "live_synthetic_runner", None)
    monkeypatch.setattr(main, "live_box_spread_runner", None)

    main._signal_live_runner_shutdown()

    assert runner.stop_event.is_set()
    assert calls == []
