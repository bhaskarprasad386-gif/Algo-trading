from pathlib import Path

from app.main import DASHBOARD_FILE, dashboard


def test_dashboard_file_exists():
    assert isinstance(DASHBOARD_FILE, Path)
    assert DASHBOARD_FILE.is_file()


def test_dashboard_serves_html():
    response = dashboard()
    assert response.media_type == "text/html"
    assert response.path == str(DASHBOARD_FILE)
