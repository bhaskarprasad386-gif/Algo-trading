from app.scanner.iv_skew_flip.alerts.orchestrator import IVEarlyEntryAlertEngine


def test_alert_engine_has_no_time_of_day_strategy_window():
    # Strategy conditions, not clock windows, decide when an alert is emitted.
    assert not hasattr(IVEarlyEntryAlertEngine, "_in_window")
