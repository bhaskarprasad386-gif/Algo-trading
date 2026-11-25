#!/usr/bin/env python3
"""Independent, read-only VPS journal collector for dashboard diagnostics."""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HOST = os.environ.get("DIAGNOSTICS_HOST", "127.0.0.1")
PORT = int(os.environ.get("DIAGNOSTICS_PORT", "8765"))
DB_PATH = Path(os.environ.get("DIAGNOSTICS_DB", "/var/lib/algo-diagnostics/events.sqlite3"))
UNIT = os.environ.get("DIAGNOSTICS_SOURCE_UNIT", "algo-trading.service")
POLL_SECONDS = max(2, int(os.environ.get("DIAGNOSTICS_POLL_SECONDS", "5")))
RETENTION_DAYS = max(1, int(os.environ.get("DIAGNOSTICS_RETENTION_DAYS", "14")))
MAX_EVENTS = max(100, int(os.environ.get("DIAGNOSTICS_MAX_EVENTS", "3000")))
STOP = threading.Event()

_ANSI_ESCAPE = re.compile(r"\\x1b\\[[0-9;]*m")
_SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"""(?i)((?:api[_-]?(?:key|secret)|access[_-]?token|refresh[_-]?token|session[_-]?token|feed[_-]?token|client[_-]?id|account[_-]?(?:id|number)|uid|token|password|passwd|secret|totp|jwt)\s*[=:]\s*)['"]?[^\s,'"]+"""), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1[REDACTED]"),
]


def sanitize(value: object, limit: int = 1200) -> str:
    text = _ANSI_ESCAPE.sub("", str(value or ""))
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in text)
    return text[:limit]


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY, cursor TEXT, timestamp TEXT NOT NULL,
        severity TEXT NOT NULL, component TEXT NOT NULL, message TEXT NOT NULL,
        source TEXT NOT NULL, inserted_at REAL NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp DESC)")
    db.execute("""CREATE TABLE IF NOT EXISTS collector_state (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    db.commit()
    return db


def state_get(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def state_set(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT INTO collector_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def classify(priority: int, message: str = "") -> str | None:
    # loguru writes to stdout; journald labels stdout as INFO even when the
    # embedded application level is ERROR/WARNING. Inspect the structured line.
    match = re.search(r"\|\s*(CRITICAL|ERROR|WARNING)\s*\|", message, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    if priority <= 2:
        return "CRITICAL"
    if priority == 3:
        return "ERROR"
    if priority == 4:
        return "WARNING"
    return None


def collect_once(db: sqlite3.Connection) -> None:
    cursor = state_get(db, "journal_cursor")
    command = ["journalctl", "--unit", UNIT, "--no-pager", "--output=json"]
    if cursor:
        # Do not cap incremental reads: advancing the cursor after a capped
        # response could silently skip a burst containing more than N events.
        command.extend(["--after-cursor", cursor])
    else:
        command.extend(["--since", "7 days ago", "--lines=3000"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode not in (0, 1):
        return

    newest_cursor = cursor
    with db:
        for raw in result.stdout.splitlines():
            try:
                item = json.loads(raw)
            except (ValueError, TypeError):
                continue
            raw_cursor = item.get("__CURSOR")
            if raw_cursor:
                newest_cursor = str(raw_cursor)
            try:
                priority = int(item.get("PRIORITY", 6))
            except (TypeError, ValueError):
                priority = 6
            try:
                stamp = datetime.fromtimestamp(int(item.get("__REALTIME_TIMESTAMP")) / 1_000_000, tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                stamp = datetime.now(timezone.utc).isoformat()
            message = sanitize(item.get("MESSAGE", ""))
            severity = classify(priority, message)
            if severity is None:
                continue
            component = sanitize(item.get("SYSLOG_IDENTIFIER") or item.get("_COMM") or UNIT, 120)
            identity = str(raw_cursor or hashlib.sha256((stamp + component + message).encode()).hexdigest())
            db.execute(
                "INSERT OR IGNORE INTO events(event_id,cursor,timestamp,severity,component,message,source,inserted_at) VALUES(?,?,?,?,?,?,?,?)",
                (identity, str(raw_cursor or ""), stamp, severity, component, message, "systemd-journal", time.time()),
            )
        if newest_cursor:
            state_set(db, "journal_cursor", newest_cursor)
        db.execute("DELETE FROM events WHERE inserted_at < ?", (time.time() - RETENTION_DAYS * 86400,))
        db.execute("DELETE FROM events WHERE event_id NOT IN (SELECT event_id FROM events ORDER BY timestamp DESC LIMIT ?)", (MAX_EVENTS,))


def collector_loop() -> None:
    db = connect_db()
    while not STOP.is_set():
        try:
            collect_once(db)
        except Exception:
            # Keep the collector alive; do not leak raw exception data to another log.
            pass
        STOP.wait(POLL_SECONDS)
    db.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "AlgoDiagnostics/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"status": "ok", "service": "algo-diagnostics-agent", "source_unit": UNIT, "server_time": datetime.now(timezone.utc).isoformat()})
            return
        if parsed.path != "/api/errors":
            self._json(404, {"status": "not_found"})
            return
        try:
            limit = max(1, min(200, int(parse_qs(parsed.query).get("limit", ["100"])[0])))
        except (TypeError, ValueError):
            limit = 100
        db = connect_db()
        try:
            rows = db.execute("SELECT event_id,timestamp,severity,component,message,source FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
            errors = [{"id": row[0], "time": row[1], "severity": row[2], "component": row[3], "message": row[4], "source": row[5]} for row in rows]
            self._json(200, {"status": "ok", "source": "vps-systemd-journal", "server_time": datetime.now(timezone.utc).isoformat(), "count": len(errors), "errors": errors})
        finally:
            db.close()


def main() -> None:
    worker = threading.Thread(target=collector_loop, name="journal-collector", daemon=True)
    worker.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True

    def stop_handler(_signum: int, _frame: object) -> None:
        STOP.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        STOP.set()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
