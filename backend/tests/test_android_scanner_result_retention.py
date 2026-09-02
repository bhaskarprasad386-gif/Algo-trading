from pathlib import Path


MAIN_ACTIVITY = Path(__file__).parents[2] / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "algotrading" / "app" / "MainActivity.kt"


def test_android_scanner_result_retention_contract():
    body = MAIN_ACTIVITY.read_text(encoding="utf-8")

    assert "private var lastScannerResult: String? = null" in body
    assert 'tvScannerResult.text = lastScannerResult?.let { "$it\\n\\nREFRESHING SCANNER..." } ?: "SCAN IN PROGRESS\\n\\nRunning Cash–Future scanner..."' in body
    assert "lastScannerResult = result" in body
    assert 'tvScannerResult.text = lastScannerResult?.let { "$it\\n\\nREFRESH FAILED\\nLast Attempt: $failedAt\\n\\nScanner Failed: ${error.message ?: "API error"}" } ?: "SCAN ERROR\\n\\nLast Scan: $failedAt\\n\\nScanner Failed: ${error.message ?: "API error}"}' not in body
    assert 'REFRESH FAILED' in body
    assert 'Scanner Failed: ${error.message ?: "API error"}' in body

    scan_block = body.split("private fun runCashFutureScanner()", 1)[1]
    assert "lastScannerResult?.let" in scan_block
    assert "lastScannerResult = result" in scan_block
    assert "REFRESH FAILED" in scan_block
