from pathlib import Path


DASHBOARD = Path(__file__).parents[2] / "web" / "dashboard" / "index.html"


def test_dashboard_auto_refresh_controls_and_scheduler():
    body = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="autoRefresh"' in body
    assert 'id="refreshSeconds"' in body
    assert 'min="10" max="300"' in body
    assert "let refreshTimer=null" in body
    assert "function scheduleScan()" in body
    assert "refreshTimer=setTimeout(scan,seconds*1000)" in body
    assert "autoRefresh').addEventListener('change',scheduleScan)" in body
    assert "refreshSeconds').addEventListener('change',scheduleScan)" in body
    assert "scheduleScan()" in body
