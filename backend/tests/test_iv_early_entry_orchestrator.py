from datetime import datetime
from zoneinfo import ZoneInfo

from app.scanner.iv_skew_flip.alerts.orchestrator import IVEarlyEntryAlertEngine


IST = ZoneInfo("Asia/Kolkata")


def test_strategy_a_window_is_early_session_and_afternoon_only():
    assert IVEarlyEntryAlertEngine._in_window(
        datetime(2026, 11, 12, 10, 0, tzinfo=IST), "IV_VOLUME_MOMENTUM"
    )
    assert not IVEarlyEntryAlertEngine._in_window(
        datetime(2026, 11, 12, 12, 0, tzinfo=IST), "IV_VOLUME_MOMENTUM"
    )
    assert IVEarlyEntryAlertEngine._in_window(
        datetime(2026, 11, 12, 14, 0, tzinfo=IST), "IV_VOLUME_MOMENTUM"
    )


def test_wall_strategy_window_and_market_hours():
    assert IVEarlyEntryAlertEngine._in_window(
        datetime(2026, 11, 12, 9, 45, tzinfo=IST), "OI_WALL_REJECTION"
    )
    assert not IVEarlyEntryAlertEngine._in_window(
        datetime(2026, 11, 12, 12, 0, tzinfo=IST), "OI_WALL_REJECTION"
    )
    assert not IVEarlyEntryAlertEngine._in_window(
        datetime(2026, 11, 14, 10, 0, tzinfo=IST), "OI_WALL_REJECTION"
    )
    assert not IVEarlyEntryAlertEngine._in_window(
        datetime(2026, 11, 12, 15, 6, tzinfo=IST), "OI_WALL_REJECTION"
    )
