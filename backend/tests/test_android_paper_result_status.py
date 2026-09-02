from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_paper_result_status_messages_are_clear():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    for marker in (
        'tvPaperResult.text = "ENTRY SUCCESS',
        'tvPaperResult.text = "ENTRY FAILED',
        'tvPaperResult.text = if (position == null) "POSITION CHECK SUCCESS',
        'tvPaperResult.text = "POSITION CHECK FAILED',
        'tvPaperResult.text = if (response.status == "closed") "EXIT SUCCESS',
        'tvPaperResult.text = "EXIT FAILED',
    ):
        assert marker in ui


def test_android_paper_result_status_keeps_completion_timestamps():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'Completed: $completedAt' in ui
    assert 'Checked: $completedAt' in ui
    assert 'Time: $failedAt' in ui
