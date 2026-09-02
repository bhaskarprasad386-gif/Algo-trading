from pathlib import Path

ANDROID_ROOT = Path(__file__).resolve().parents[2] / "mobile" / "android"
API_SERVICE = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "ApiService.kt"
MAIN_ACTIVITY = ANDROID_ROOT / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_paper_execution_endpoints_match_backend_paths():
    api = API_SERVICE.read_text(encoding="utf-8")
    assert '@POST("/api/v1/execution/paper/entry")' in api
    assert '@GET("/api/v1/execution/paper/position")' in api
    assert '@POST("/api/v1/execution/paper/exit")' in api
    assert "data class PaperEntryRequest" in api
    assert "data class PaperExitRequest" in api
    assert "data class PaperPositionResponse" in api


def test_android_paper_execution_ui_uses_typed_api_responses():
    ui = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "ApiService.retrofitService.paperEntry(PaperEntryRequest(price, quantity))" in ui
    assert "ApiService.retrofitService.paperPosition()" in ui
    assert "ApiService.retrofitService.paperExit(PaperExitRequest(price))" in ui
    assert "response.entry_price" in ui
    assert "response.stop_loss" in ui
    assert "response.target" in ui
    assert "response.pnl" in ui
    assert "response.exit_price" in ui
