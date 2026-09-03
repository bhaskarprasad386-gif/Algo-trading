from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PAPER_ROUTES = BACKEND_ROOT / "app" / "execution" / "paper_routes.py"
ANDROID_API = (
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
    / "ApiService.kt"
)


def test_cash_future_scanner_has_authenticated_paper_bridge():
    source = PAPER_ROUTES.read_text(encoding="utf-8")
    assert '@router.post("/paper/from-scanner")' in source
    assert "ScannerPaperEntryRequest" in source
    assert 'result["source"] = "cash-future-scanner"' in source
    assert 'transaction_type="BUY"' in source
    assert "price=request.cash_price" in source


def test_scanner_paper_bridge_validates_executable_opportunity():
    source = PAPER_ROUTES.read_text(encoding="utf-8")
    assert "executable: bool = True" in source
    assert 'if not request.executable:' in source
    assert '"Scanner opportunity is not executable"' in source
    assert "future_price: float | None" in source
    assert "net_profit: float | None" in source
    assert "request.future_price <= request.cash_price" in source
    assert "request.net_profit <= 0" in source


def test_android_exposes_cash_future_paper_bridge():
    source = ANDROID_API.read_text(encoding="utf-8")
    assert '@POST("/api/v1/execution/paper/from-scanner")' in source
    assert "ScannerPaperEntryRequest" in source
    assert "ScannerPaperEntryResponse" in source
    assert "paperEntryFromScanner" in source
