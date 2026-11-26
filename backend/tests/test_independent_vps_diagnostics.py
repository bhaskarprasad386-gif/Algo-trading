from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "deploy" / "server_diagnostics_agent.py"
SPEC = importlib.util.spec_from_file_location("server_diagnostics_agent", MODULE_PATH)
assert SPEC and SPEC.loader
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def test_sanitize_redacts_common_credentials() -> None:
    message = "request failed api_key=abc123 password='secret-value' Authorization: Bearer abc.def.ghi"
    result = agent.sanitize(message)
    assert "abc123" not in result
    assert "secret-value" not in result
    assert "abc.def.ghi" not in result
    extended = agent.sanitize('x-api-key=one client_secret="two" pwd=three otp=four account_number=123456 Authorization: Basic c2VjcmV0')
    for secret in ("one", "two", "three", "four", "123456", "c2VjcmV0"):
        assert secret not in extended
    assert "[REDACTED]" in result


def test_sanitize_preserves_diagnostic_context_and_redacts_only_secrets() -> None:
    message = (
        "RuntimeError: Shared Index Runner failed while subscribing NIFTY-WEEKLY "
        "at backend/app/market_data/runner.py:217\\n"
        "Traceback (most recent call last):\\n"
        "  File \\\"runner.py\\\", line 217, in subscribe\\n"
        "requests.exceptions.Timeout: broker endpoint timed out after 2.0s; "
        "api_key=live-key-123 access_token='access-secret-456' "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.signature "
        "https://broker-user:broker-pass@example.test/api"
    )
    result = agent.sanitize(message)
    for detail in (
        "RuntimeError: Shared Index Runner failed", "NIFTY-WEEKLY",
        "backend/app/market_data/runner.py:217", "Traceback (most recent call last)",
        'File "runner.py", line 217', "requests.exceptions.Timeout",
        "broker endpoint timed out after 2.0s", "https://[REDACTED]@example.test/api",
    ):
        assert detail in result
    for secret in ("live-key-123", "access-secret-456", "eyJhbGciOiJIUzI1NiJ9.signature", "broker-user", "broker-pass"):
        assert secret not in result


def test_sanitize_redacts_json_query_and_private_key_secrets() -> None:
    message = (
        '{"refresh_token":"refresh-secret","status":"broker rejected order"} '
        "GET /login?api_key=query-secret&mode=live "
        "-----BEGIN PRIVATE KEY-----\\nprivate-material\\n-----END PRIVATE KEY-----"
    )
    result = agent.sanitize(message)
    for secret in ("refresh-secret", "query-secret", "private-material"):
        assert secret not in result
    assert "broker rejected order" in result
    assert "mode=live" in result
    assert "[PRIVATE KEY REDACTED]" in result

def test_classify_journal_priorities() -> None:
    assert agent.classify(0) == "CRITICAL"
    assert agent.classify(3) == "ERROR"
    assert agent.classify(4) == "WARNING"
    # loguru records arrive via stdout and journald labels them PRIORITY=6.
    assert agent.classify(6, "2026-11-26 | ERROR    | app.main:run:10 - failed") == "ERROR"
    ansi = "\x1b[31m2026-11-26 | ERROR    | app.main:run:10 - failed\x1b[0m"
    assert agent.classify(6, agent.sanitize(ansi)) == "ERROR"
    assert agent.classify(6, "normal stdout info") is None


def test_database_initializes_with_empty_event_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent, "DB_PATH", tmp_path / "events.sqlite3")
    db = agent.connect_db()
    try:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        db.close()


def test_api_runtime_events_are_persisted_without_api_process_dependency(tmp_path: Path, monkeypatch) -> None:
    import json
    from datetime import datetime, timezone

    monkeypatch.setattr(agent, "DB_PATH", tmp_path / "events.sqlite3")
    event = {
        "id": "1700000000.123",
        "time": datetime.now(timezone.utc).isoformat(),
        "severity": "ERROR",
        "component": "Shared Runner",
        "message": "worker failed api_key=hidden-value",
    }

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"errors": [event]}).encode()

    monkeypatch.setattr(agent, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    db = agent.connect_db()
    try:
        agent.collect_api_diagnostics(db)
        row = db.execute("SELECT severity,component,message,source FROM events").fetchone()
        assert row == ("ERROR", "Shared Runner", "worker failed api_key=[REDACTED]", "runtime-diagnostics-api")
        assert agent._API_STATUS["reachable"] is True
    finally:
        db.close()


def test_api_unreachable_does_not_clear_persisted_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent, "DB_PATH", tmp_path / "events.sqlite3")
    monkeypatch.setattr(agent, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("offline")))
    db = agent.connect_db()
    try:
        db.execute(
            "INSERT INTO events(event_id,cursor,timestamp,severity,component,message,source,inserted_at) VALUES(?,?,?,?,?,?,?,?)",
            ("kept", "", "2026-11-26T00:00:00+00:00", "ERROR", "API", "last known error", "systemd-journal", 1),
        )
        db.commit()
        agent.collect_api_diagnostics(db)
        row = db.execute("SELECT message FROM events WHERE event_id='kept'").fetchone()
        assert row == ("last known error",)
        assert agent._API_STATUS["reachable"] is False
    finally:
        db.close()


def test_journal_collector_persists_loguru_errors_and_advances_cursor(tmp_path: Path, monkeypatch) -> None:
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(agent, "DB_PATH", tmp_path / "events.sqlite3")
    records = [
        {"__CURSOR": "cursor-1", "__REALTIME_TIMESTAMP": "1795651200000000", "PRIORITY": "6", "SYSLOG_IDENTIFIER": "uvicorn", "MESSAGE": "2026-11-26 | ERROR    | app.main:worker:10 - worker crashed"},
        {"__CURSOR": "cursor-2", "__REALTIME_TIMESTAMP": "1795651201000000", "PRIORITY": "6", "SYSLOG_IDENTIFIER": "uvicorn", "MESSAGE": "2026-11-26 | WARNING  | app.main:worker:11 - retrying"},
        {"__CURSOR": "cursor-3", "__REALTIME_TIMESTAMP": "1795651202000000", "PRIORITY": "6", "SYSLOG_IDENTIFIER": "uvicorn", "MESSAGE": "normal informational output"},
    ]
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="".join(json.dumps(item) + "\n" for item in records), stderr="")

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    db = agent.connect_db()
    try:
        agent.collect_once(db)
        rows = db.execute("SELECT severity,message FROM events ORDER BY timestamp").fetchall()
        assert [row[0] for row in rows] == ["ERROR", "WARNING"]
        assert db.execute("SELECT value FROM collector_state WHERE key='journal_cursor'").fetchone() == ("cursor-3",)
        agent.collect_once(db)
        assert "--after-cursor" in calls[1]
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (2,)
    finally:
        db.close()


def test_journal_cursor_rotation_falls_back_and_resumes(tmp_path: Path, monkeypatch) -> None:
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(agent, "DB_PATH", tmp_path / "events.sqlite3")
    calls = []
    record = {
        "__CURSOR": "rotated-cursor",
        "__REALTIME_TIMESTAMP": "1795651200000000",
        "PRIORITY": "3",
        "SYSLOG_IDENTIFIER": "uvicorn",
        "MESSAGE": "worker exception",
    }

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "--after-cursor" in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="Failed to seek to cursor")
        return SimpleNamespace(returncode=0, stdout=json.dumps(record) + "\n", stderr="")

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    db = agent.connect_db()
    try:
        agent.state_set(db, "journal_cursor", "expired-cursor")
        agent.collect_once(db)
        assert len(calls) == 2
        assert "--since" in calls[1]
        assert db.execute("SELECT value FROM collector_state WHERE key='journal_cursor'").fetchone() == ("rotated-cursor",)
        assert db.execute("SELECT COUNT(*) FROM events").fetchone() == (1,)
    finally:
        db.close()
