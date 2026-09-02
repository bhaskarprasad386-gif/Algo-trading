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
    assert "/api/v1/execution/paper/position" in body
    assert "/api/v1/execution/paper/exit" in body
    assert '"price",exit' in body or 'price:exit' in body
    assert "pnl_pct" not in body


def test_dashboard_contains_paper_position_check_ui():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert 'onclick="paperPosition()"' in body
    assert "Check Position" in body
    assert "CHECKING PAPER POSITION…" in body
    assert "PAPER POSITION ACTIVE" in body
    assert "No active paper position." in body


def test_dashboard_paper_actions_have_busy_state_protection():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert 'const paperButtons=[\'paperEntryBtn\',\'paperPositionBtn\',\'paperExitBtn\'];' in body
    assert 'function setPaperBusy(busy,message)' in body
    assert 'document.getElementById(id).disabled=busy' in body
    assert "setPaperBusy(true,'PAPER ENTRY IN PROGRESS…')" in body
    assert "setPaperBusy(true,'CHECKING PAPER POSITION…')" in body
    assert "setPaperBusy(true,'PAPER EXIT IN PROGRESS…')" in body
    assert "finally{setPaperBusy(false)}" in body


def test_dashboard_scanner_has_busy_state_protection():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert 'id="scanBtn"' in body
    assert "button.disabled=true" in body
    assert "button.textContent='SCANNING…'" in body
    assert "status.textContent='SCANNING BACKEND…'" in body
    assert "button.disabled=false" in body
    assert "button.textContent='RUN SCAN'" in body
    assert "finally{button.disabled=false;button.textContent='RUN SCAN'}" in body
