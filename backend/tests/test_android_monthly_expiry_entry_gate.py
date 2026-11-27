from pathlib import Path

ANDROID_ACTIVITY = (
    Path(__file__).resolve().parents[2]
    / "mobile"
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "algotrading"
    / "app"
    / "FullFnoBacktestActivity.kt"
)


def test_full_fno_android_screen_has_no_retired_cash_future_builder_or_replay():
    source = ANDROID_ACTIVITY.read_text(encoding="utf-8")
    assert "startFullFnoJob()" in source
    assert "fullFnoResults(" in source
    assert "cashFutureReplay(" not in source
    assert "CashFutureStrategyBuilderView" not in source
    assert "DailyGapCalendar" not in source
    assert "Historical ranking retained" not in source
