"""Durable SQLite state for resumable backtest jobs and chunks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable


JOB_STATES = {"queued", "running", "progress", "completed", "failed", "recoverable", "cancelled"}
CHUNK_STATES = {"pending", "running", "completed", "failed", "recoverable", "skipped"}


class BacktestJobStore:
    """Persist backtest job/chunk checkpoints independently of trade rows."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_jobs (
                    job_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    plan_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    completed_chunks INTEGER NOT NULL DEFAULT 0,
                    failed_chunk INTEGER,
                    processed_events INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_job_chunks (
                    job_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    processed_events INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    PRIMARY KEY (job_id, chunk_index),
                    FOREIGN KEY (job_id) REFERENCES backtest_jobs(job_id) ON DELETE CASCADE
                )
                """
            )

    @staticmethod
    def fingerprint(parts: Iterable[object]) -> str:
        payload = json.dumps(tuple(parts), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def create(self, job_id: str, run_id: str, plan_fingerprint: str, total_chunks: int) -> None:
        if not job_id.strip() or not run_id.strip() or not plan_fingerprint.strip():
            raise ValueError("job_id, run_id and plan_fingerprint are required")
        if total_chunks < 0:
            raise ValueError("total_chunks must be non-negative")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO backtest_jobs(job_id, run_id, plan_fingerprint, state, total_chunks) "
                "VALUES (?, ?, ?, 'queued', ?)",
                (job_id, run_id, plan_fingerprint, total_chunks),
            )
            connection.executemany(
                "INSERT INTO backtest_job_chunks(job_id, chunk_index, state) VALUES (?, ?, 'pending')",
                ((job_id, index) for index in range(total_chunks)),
            )

    def get(self, job_id: str) -> tuple[object, ...] | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT job_id, run_id, plan_fingerprint, state, total_chunks, "
                "completed_chunks, failed_chunk, processed_events, error "
                "FROM backtest_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()

    def pending_indices(self, job_id: str) -> tuple[int, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT chunk_index FROM backtest_job_chunks "
                "WHERE job_id=? AND state IN ('pending','failed','recoverable') ORDER BY chunk_index",
                (job_id,),
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def recover_running_chunks(self, job_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE backtest_job_chunks SET state='recoverable', error='recovered after restart' "
                "WHERE job_id=? AND state='running'",
                (job_id,),
            )
            connection.execute(
                "UPDATE backtest_jobs SET state='recoverable', error='recovered after restart' "
                "WHERE job_id=? AND state='running'",
                (job_id,),
            )
        return cursor.rowcount

    def start_chunk(self, job_id: str, chunk_index: int) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM backtest_job_chunks WHERE job_id=? AND chunk_index=?",
                (job_id, chunk_index),
            ).fetchone()
            if row is None:
                raise ValueError("unknown backtest chunk")
            if row[0] == "completed":
                return
            connection.execute(
                "UPDATE backtest_job_chunks SET state='running', attempts=attempts+1, error=NULL "
                "WHERE job_id=? AND chunk_index=?",
                (job_id, chunk_index),
            )
            connection.execute(
                "UPDATE backtest_jobs SET state='running', failed_chunk=NULL, error=NULL WHERE job_id=?",
                (job_id,),
            )

    def complete_chunk(self, job_id: str, chunk_index: int, processed_events: int) -> None:
        if processed_events < 0:
            raise ValueError("processed_events must be non-negative")
        with self._connect() as connection:
            connection.execute(
                "UPDATE backtest_job_chunks SET state='completed', processed_events=?, error=NULL "
                "WHERE job_id=? AND chunk_index=?",
                (processed_events, job_id, chunk_index),
            )
            self._refresh_counts(connection, job_id)

    def fail_chunk(self, job_id: str, chunk_index: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE backtest_job_chunks SET state='failed', error=? WHERE job_id=? AND chunk_index=?",
                (error, job_id, chunk_index),
            )
            connection.execute(
                "UPDATE backtest_jobs SET state='failed', failed_chunk=?, error=? WHERE job_id=?",
                (chunk_index, error, job_id),
            )
            self._refresh_counts(connection, job_id)

    def finish(self, job_id: str) -> None:
        with self._connect() as connection:
            self._refresh_counts(connection, job_id)
            pending = connection.execute(
                "SELECT COUNT(*) FROM backtest_job_chunks WHERE job_id=? AND state != 'completed'",
                (job_id,),
            ).fetchone()[0]
            state = "completed" if pending == 0 else "progress"
            connection.execute("UPDATE backtest_jobs SET state=? WHERE job_id=?", (state, job_id))

    @staticmethod
    def _refresh_counts(connection: sqlite3.Connection, job_id: str) -> None:
        row = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(processed_events), 0) "
            "FROM backtest_job_chunks WHERE job_id=? AND state='completed'",
            (job_id,),
        ).fetchone()
        connection.execute(
            "UPDATE backtest_jobs SET completed_chunks=?, processed_events=? WHERE job_id=?",
            (int(row[0]), int(row[1]), job_id),
        )
