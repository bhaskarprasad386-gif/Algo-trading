from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_paper_position_summary_contains_core_fields():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'tvPaperResult.text = if (position == null)' in ui
    assert '"POSITION CHECK SUCCESS\\n\\nPAPER POSITION: FLAT' in ui
    assert '"POSITION CHECK SUCCESS\\n\\nPAPER POSITION ACTIVE\\nChecked: $completedAt' in ui
    assert 'Entry: ₹${position.entry_price}' in ui
    assert 'Stop Loss: ₹${position.stop_loss}' in ui
    assert 'Target: ₹${position.target}' in ui
    assert 'Quantity: ${position.quantity}' in ui


def test_android_paper_entry_summary_contains_core_fields():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'tvPaperResult.text = "ENTRY SUCCESS' in ui
    assert '"PAPER POSITION ACTIVE\\nCompleted: $completedAt' in ui
    assert 'Entry: ₹${response.entry_price}' in ui
    assert 'Stop Loss: ₹${response.stop_loss}' in ui
    assert 'Target: ₹${response.target}' in ui
    assert 'Quantity: ${response.position.quantity}' in ui
