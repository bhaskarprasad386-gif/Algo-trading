from pathlib import Path

from app.main import DASHBOARD_FILE, dashboard


def test_dashboard_file_exists():
    assert isinstance(DASHBOARD_FILE, Path)
    assert DASHBOARD_FILE.is_file()


def test_dashboard_serves_html_and_scanner_connector():
    response = dashboard()
    assert response.media_type == "text/html"
    assert "Cash–Future Opportunities" in response.body.decode("utf-8")
    assert "/api/v1/scanner/cash-future/live/auto" in response.body.decode("utf-8")
