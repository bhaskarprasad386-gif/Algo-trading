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
    assert "/api/v1/scanner/cash-future/live/fast" in body


def test_dashboard_does_not_expose_paper_execution_controls():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert "/api/v1/execution/paper/" not in body
    assert "/api/v1/live-paper/" not in body
    assert "PAPER ENTRY" not in body
    assert "PAPER EXIT" not in body
    assert "Paper Portfolio" not in body


def test_dashboard_scanner_has_busy_state_protection():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert 'id="scanBtn"' in body
    assert "button.disabled=true" in body
    assert "button.textContent='SCANNING…'" in body
    assert "status.textContent='READING LIVE 1s SCANNER…'" in body
    assert "button.disabled=false" in body
    assert "button.textContent='RUN LIVE SCAN'" in body


def test_dashboard_scanner_shows_last_scan_time():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert 'id="lastScan"' in body
    assert "Last Scan:" in body
    assert 'document.getElementById(\'lastScan\').textContent=stamp()' in body
    assert 'function stamp(){return new Date().toLocaleString()}' in body


def test_dashboard_scanner_shows_summary_metrics():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert 'id="scanSummary"' in body
    assert 'id="summarySymbols"' in body
    assert 'id="summaryObservations"' in body
    assert 'id="summaryOpportunities"' in body
    assert 'id="summaryErrors"' in body
    assert 'function setScanSummary(p)' in body
    assert 'setScanSummary(p)' in body


def test_dashboard_scanner_prioritizes_executable_opportunities():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert "Number(b.rank_score||0)-Number(a.rank_score||0)" in body
    assert "Number(b.gap_pct||0)-Number(a.gap_pct||0)" in body
    assert "Number(b.net_profit||0)-Number(a.net_profit||0)" in body
    assert "tr.innerHTML='<td>'+(i+1)+'" in body
    assert "<th>Rank</th>" in body


def test_dashboard_scanner_has_clear_empty_and_failure_states():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert "NO LIVE SIGNAL WITHIN 5 SECONDS" in body
    assert "SCAN COMPLETE • NO LIVE SIGNAL WITHIN 5 SECONDS" in body
    assert "No backend data — no demo data shown." not in body
    assert "status.textContent='LIVE SCAN FAILED: '+e.message" in body
    assert "log('Live scan failed: '+e.message)" in body


def test_dashboard_scanner_has_auto_refresh_controls():
    response = dashboard()
    body = response.body.decode("utf-8")
    assert 'id="autoRefresh"' in body
    assert 'id="refreshSeconds"' in body
    assert 'min="10" max="300"' in body
    assert 'let refreshTimer=null;' in body
    assert "function scheduleScan()" in body
    assert "setTimeout(async()=>{" in body
    assert "seconds*1000" in body
    assert "document.getElementById('autoRefresh').addEventListener('change',scheduleScan)" in body
