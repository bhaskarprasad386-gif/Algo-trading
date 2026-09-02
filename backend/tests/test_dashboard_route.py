from pathlib import Path

from app.main import DASHBOARD_FILE, dashboard


def test_dashboard_file_exists():
    assert isinstance(DASHBOARD_FILE, Path)
    assert DASHBOARD_FILE.is_file()


def test_dashboard_serves_html_and_scanner_connector():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert response.media_type == "text/html"
    assert "Cash–Future Opportunities" in body
    assert "/api/v1/scanner/cash-future/live/auto" in body


def test_dashboard_contains_paper_execution_connectors():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert "/api/v1/execution/paper/entry" in body
    assert "/api/v1/execution/paper/exit" in body
    assert '"price",exit' in body or 'price:exit' in body
    assert "pnl_pct" not in body
