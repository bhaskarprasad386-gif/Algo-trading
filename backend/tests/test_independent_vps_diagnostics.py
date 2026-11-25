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
    assert "[REDACTED]" in result


def test_classify_journal_priorities() -> None:
    assert agent.classify(0) == "CRITICAL"
    assert agent.classify(3) == "ERROR"
    assert agent.classify(4) == "WARNING"
    # loguru records arrive via stdout and journald labels them PRIORITY=6.
    assert agent.classify(6, "2026-11-26 | ERROR    | app.main:run:10 - failed") == "ERROR"
    ansi = "\\x1b[31m2026-11-26 | ERROR    | app.main:run:10 - failed\\x1b[0m"
    assert agent.classify(6, agent.sanitize(ansi)) == "ERROR"
    assert agent.classify(6, "normal stdout info") is None


def test_database_initializes_with_empty_event_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent, "DB_PATH", tmp_path / "events.sqlite3")
    db = agent.connect_db()
    try:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        db.close()
