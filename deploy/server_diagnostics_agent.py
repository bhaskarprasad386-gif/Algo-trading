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
from urllib.request import Request, urlopen
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
API_URL = os.environ.get("DIAGNOSTICS_API_URL", "http://127.0.0.1:8000/api/v1/diagnostics/errors?limit=100")
API_TIMEOUT_SECONDS = max(0.5, float(os.environ.get("DIAGNOSTICS_API_TIMEOUT_SECONDS", "2")))
# Preserve multi-frame tracebacks and broker response context while keeping the
# bounded SQLite store predictable (3,000 events x 16 KiB maximum).
_API_STATUS_LOCK = threading.Lock()
_API_STATUS = {"reachable": False, "last_success_at": None}

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_SENSITIVE_KEYS = (
    r"x[-_]?private[-_]?key|x[-_]?api[-_]?key|api[-_]?key|private[-_]?key|"
    r"x[-_]?feed[-_]?token|feed[-_]?token|jwt[-_]?token|access[-_]?token|"
    r"refresh[-_]?token|auth[-_]?token|(?:api[-_]?)?token|"
    r"account[-_]?(?:id|number)|uid|password|passwd|pwd|"
    r"totp(?:[-_]?secret)?|one[-_]?time[-_]?password|otp|"
    r"client[-_]?secret|client[-_]?code|client[-_]?id|api[-_]?secret|secret"
)
_QUOTED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEYS}|authorization)['\"]?\s*[:=]\s*['\"])"
    rf"(?P<value>[^'\"\r\n]*)(?P<suffix>['\"])",
    re.IGNORECASE,
)
_PLAIN_ASSIGNMENT = re.compile(
    rf"(?P<prefix>\b(?:{_SENSITIVE_KEYS}|authorization)\b\s*[:=]\s*)"
    rf"(?P<value>(?:Bearer|Basic)\s+[^,\s;}}\]&]+|[^,\s;}}\]&]+)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s:]+:[^/@\s]+@", re.IGNORECASE)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)


def sanitize(value: object, limit: int = 16000) -> str:
    """Hide credential values while retaining the surrounding diagnostic text."""
    text = _ANSI_ESCAPE.sub("", str(value or ""))
    text = _PRIVATE_KEY_BLOCK.sub("[PRIVATE KEY REDACTED]", text)
    text = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", text)
    text = _QUOTED_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
        text,
    )
    text = _PLAIN_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        text,
    )
    text = _BEARER_VALUE.sub(r"\1 [REDACTED]", text)
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



def collect_api_diagnostics(db: sqlite3.Connection) -> None:
    """Persist process-local runtime diagnostics while the API is still reachable."""
    try:
        request = Request(API_URL, headers={"Accept": "application/json"}, method="GET")
        with urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise OSError("diagnostics API returned non-200")
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("errors"), list):
            raise ValueError("diagnostics API schema is invalid")
    except Exception:
        with _API_STATUS_LOCK:
            _API_STATUS["reachable"] = False
        return

    now = time.time()
    with db:
        for event in payload["errors"][:100]:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                continue
            severity = str(event.get("severity") or "ERROR").upper()
            if severity not in {"CRITICAL", "ERROR", "WARNING"}:
                continue
            raw_time = str(event.get("time") or "")
            try:
                stamp = datetime.fromisoformat(raw_time.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                stamp = datetime.now(timezone.utc).isoformat()
            component = sanitize(event.get("component") or "Runtime", 120)
            message = sanitize(event.get("message") or event.get("error_type") or "Runtime diagnostic event")
            db.execute(
                "INSERT OR IGNORE INTO events(event_id,cursor,timestamp,severity,component,message,source,inserted_at) VALUES(?,?,?,?,?,?,?,?)",
                ("api:" + event_id, "", stamp, severity, component, message, "runtime-diagnostics-api", now),
            )
    with _API_STATUS_LOCK:
        _API_STATUS["reachable"] = True
        _API_STATUS["last_success_at"] = datetime.now(timezone.utc).isoformat()

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
        recovered_cursor = False
        if result.returncode != 0 and cursor:
            # Journal rotation can invalidate an old cursor. Replay a bounded
            # recent window and rely on event IDs for deduplication.
            fallback = [
                "journalctl", "--unit", UNIT, "--no-pager", "--output=json",
                "--since", "7 days ago", "--lines=3000",
            ]
            result = subprocess.run(fallback, capture_output=True, text=True, timeout=12, check=False)
            if result.returncode not in (0, 1):
                return
            recovered_cursor = True
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode not in (0, 1):
        return

    newest_cursor = None if recovered_cursor else cursor
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
        elif recovered_cursor:
            state_set(db, "journal_cursor", "")
        db.execute("DELETE FROM events WHERE inserted_at < ?", (time.time() - RETENTION_DAYS * 86400,))
        db.execute("DELETE FROM events WHERE event_id NOT IN (SELECT event_id FROM events ORDER BY timestamp DESC LIMIT ?)", (MAX_EVENTS,))


def collector_loop() -> None:
    db: sqlite3.Connection | None = None
    while not STOP.is_set():
        try:
            if db is None:
                db = connect_db()
            collect_once(db)
            collect_api_diagnostics(db)
        except Exception:
            # Recover from transient database/journal failures without terminating
            # the independent service or leaking raw exception data.
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
                db = None
        STOP.wait(POLL_SECONDS)
    if db is not None:
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

    def _method_not_allowed(self) -> None:
        payload = json.dumps({"status": "method_not_allowed", "allowed": ["GET"]}).encode()
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            try:
                db = connect_db()
                db.execute("SELECT 1").fetchone()
                db.close()
                database_ok = True
            except Exception:
                database_ok = False
            with _API_STATUS_LOCK:
                api_status = dict(_API_STATUS)
            self._json(200 if database_ok else 503, {"status": "ok" if database_ok else "degraded", "service": "algo-diagnostics-agent", "database_ok": database_ok, "source_unit": UNIT, "api_process_reachable": api_status["reachable"], "api_last_success_at": api_status["last_success_at"], "server_time": datetime.now(timezone.utc).isoformat()})
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
            with _API_STATUS_LOCK:
                api_status = dict(_API_STATUS)
            self._json(200, {"status": "ok", "source": "vps-journal+runtime-diagnostics", "api_process_reachable": api_status["reachable"], "api_last_success_at": api_status["last_success_at"], "server_time": datetime.now(timezone.utc).isoformat(), "count": len(errors), "errors": errors})
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
