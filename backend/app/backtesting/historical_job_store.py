"""Durable job/chunk state for restart-safe historical acquisition."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping


_JOB_STATES = {"queued", "running", "progress", "completed", "failed", "cancelled", "recoverable"}
_CHUNK_STATES = {"pending", "running", "completed", "skipped", "failed", "recoverable"}


@dataclass(frozen=True)
class HistoricalJob:
    job_id: str
    run_id: str
    plan_fingerprint: str
    state: str
    total_chunks: int
    completed_chunks: int
    skipped_chunks: int
    failed_chunk: int | None
    error: str | None


class HistoricalJobStore:
    """SQLite-backed job ledger; only scalar metadata is persisted, never raw market rows."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("""CREATE TABLE IF NOT EXISTS historical_jobs (
            job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, plan_fingerprint TEXT NOT NULL,
            plan_metadata TEXT, state TEXT NOT NULL, total_chunks INTEGER NOT NULL, completed_chunks INTEGER NOT NULL DEFAULT 0,
            skipped_chunks INTEGER NOT NULL DEFAULT 0, failed_chunk INTEGER, error TEXT,
            CHECK(state IN ('queued','running','progress','completed','failed','cancelled','recoverable'))
        )""")
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(historical_jobs)").fetchall()}
        if "plan_metadata" not in columns:
            self._db.execute("ALTER TABLE historical_jobs ADD COLUMN plan_metadata TEXT")
        self._db.execute("""CREATE TABLE IF NOT EXISTS historical_job_chunks (
            job_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
            PRIMARY KEY(job_id, chunk_index),
            FOREIGN KEY(job_id) REFERENCES historical_jobs(job_id) ON DELETE CASCADE,
            CHECK(state IN ('pending','running','completed','skipped','failed','recoverable'))
        )""")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def fingerprint(requests: tuple[Mapping[str, Any], ...]) -> str:
        canonical = json.dumps([dict(r) for r in requests], sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def create(self, *, job_id: str, run_id: str, plan_fingerprint: str, total_chunks: int, plan_metadata: tuple[Mapping[str, Any], ...] | None = None) -> HistoricalJob:
        if not job_id.strip() or not run_id.strip() or not plan_fingerprint.strip():
            raise ValueError("job_id, run_id and plan_fingerprint are required")
        if total_chunks < 0:
            raise ValueError("total_chunks cannot be negative")
        metadata_json = None if plan_metadata is None else json.dumps([dict(item) for item in plan_metadata], sort_keys=True, separators=(",", ":"), default=str)
        self._db.execute(
            "INSERT INTO historical_jobs(job_id,run_id,plan_fingerprint,plan_metadata,state,total_chunks) VALUES(?,?,?,?,?,?)",
            (job_id, run_id, plan_fingerprint, metadata_json, "queued", total_chunks),
        )
        self._db.executemany(
            "INSERT INTO historical_job_chunks(job_id,chunk_index,state) VALUES(?,?,?)",
            ((job_id, i, "pending") for i in range(total_chunks)),
        )
        self._db.commit()
        return self.get(job_id)

    def get(self, job_id: str) -> HistoricalJob:
        row = self._db.execute(
            "SELECT job_id,run_id,plan_fingerprint,state,total_chunks,completed_chunks,skipped_chunks,failed_chunk,error FROM historical_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return HistoricalJob(*row)

    def plan_metadata(self, job_id: str) -> tuple[dict[str, Any], ...] | None:
        """Return the exact persisted request metadata for a durable job, if available."""
        self.get(job_id)
        row = self._db.execute("SELECT plan_metadata FROM historical_jobs WHERE job_id=?", (job_id,)).fetchone()
        assert row is not None
        if row[0] is None:
            return None
        payload = json.loads(str(row[0]))
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError(f"invalid persisted plan metadata for job {job_id}")
        return tuple(dict(item) for item in payload)

    def _require_chunk(self, job_id: str, chunk_index: int) -> None:
        if chunk_index < 0:
            raise ValueError("chunk_index cannot be negative")
        exists = self._db.execute(
            "SELECT 1 FROM historical_job_chunks WHERE job_id=? AND chunk_index=?",
            (job_id, chunk_index),
        ).fetchone()
        if exists is None:
            raise KeyError(f"unknown chunk {chunk_index} for job {job_id}")

    def chunk_state(self, job_id: str, chunk_index: int) -> tuple[str, int, str | None]:
        self._require_chunk(job_id, chunk_index)
        row = self._db.execute(
            "SELECT state, attempts, error FROM historical_job_chunks WHERE job_id=? AND chunk_index=?",
            (job_id, chunk_index),
        ).fetchone()
        assert row is not None
        return str(row[0]), int(row[1]), row[2]

    def pending_indices(self, job_id: str) -> tuple[int, ...]:
        self.get(job_id)
        rows = self._db.execute(
            "SELECT chunk_index FROM historical_job_chunks WHERE job_id=? AND state IN ('pending','recoverable') ORDER BY chunk_index",
            (job_id,),
        ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def recover_running_chunks(self, job_id: str) -> tuple[int, ...]:
        """Recover chunks left running by a crashed worker before a new run starts."""
        self.get(job_id)
        rows = self._db.execute(
            "SELECT chunk_index FROM historical_job_chunks WHERE job_id=? AND state='running' ORDER BY chunk_index",
            (job_id,),
        ).fetchall()
        recovered = tuple(int(row[0]) for row in rows)
        if recovered:
            self._db.execute(
                "UPDATE historical_job_chunks SET state='recoverable', error=COALESCE(error, 'recovered after interrupted run') WHERE job_id=? AND state='running'",
                (job_id,),
            )
            self._db.execute("UPDATE historical_jobs SET state='recoverable' WHERE job_id=?", (job_id,))
            self._db.commit()
        return recovered

    def cancel(self, job_id: str, *, reason: str = "cancelled by user") -> HistoricalJob:
        """Persist a cooperative cancellation request without deleting resumable chunks."""
        job = self.get(job_id)
        if not str(reason).strip():
            raise ValueError("reason is required")
        if job.state in {"completed", "failed", "cancelled"}:
            raise ValueError(f"job {job_id} cannot be cancelled from state {job.state}")
        self._db.execute(
            "UPDATE historical_jobs SET state='cancelled', error=? WHERE job_id=?",
            (reason, job_id),
        )
        self._db.commit()
        return self.get(job_id)

    def reopen_cancelled(self, job_id: str) -> HistoricalJob:
        """Resume a cancelled job while preserving completed chunks and pending work."""
        job = self.get(job_id)
        if job.state != "cancelled":
            raise ValueError(f"job {job_id} is not cancelled")
        self._db.execute(
            "UPDATE historical_jobs SET state='progress', error=NULL WHERE job_id=?",
            (job_id,),
        )
        self._db.commit()
        return self.get(job_id)

    def start_chunk(self, job_id: str, chunk_index: int) -> None:
        self.get(job_id)
        self._require_chunk(job_id, chunk_index)
        state, _, _ = self.chunk_state(job_id, chunk_index)
        if state not in {"pending", "recoverable"}:
            raise ValueError(f"chunk {chunk_index} cannot start from state {state}")
        self._db.execute(
            "UPDATE historical_job_chunks SET state='running', attempts=attempts+1, error=NULL WHERE job_id=? AND chunk_index=?",
            (job_id, chunk_index),
        )
        self._db.execute("UPDATE historical_jobs SET state='running' WHERE job_id=?", (job_id,))
        self._db.commit()

    def complete_chunk(self, job_id: str, chunk_index: int, *, skipped: bool = False) -> None:
        self.get(job_id)
        self._require_chunk(job_id, chunk_index)
        state, _, _ = self.chunk_state(job_id, chunk_index)
        if state not in {"running", "pending", "recoverable"}:
            raise ValueError(f"chunk {chunk_index} cannot complete from state {state}")
        next_state = "skipped" if skipped else "completed"
        self._db.execute(
            "UPDATE historical_job_chunks SET state=?, error=NULL WHERE job_id=? AND chunk_index=?",
            (next_state, job_id, chunk_index),
        )
        self._refresh_counts(job_id)

    def fail_chunk(self, job_id: str, chunk_index: int, error: str, *, recoverable: bool = True) -> None:
        self.get(job_id)
        self._require_chunk(job_id, chunk_index)
        if not str(error).strip():
            raise ValueError("error is required")
        state, _, _ = self.chunk_state(job_id, chunk_index)
        if state not in {"running", "pending", "recoverable"}:
            raise ValueError(f"chunk {chunk_index} cannot fail from state {state}")
        next_state = "recoverable" if recoverable else "failed"
        self._db.execute(
            "UPDATE historical_job_chunks SET state=?, error=? WHERE job_id=? AND chunk_index=?",
            (next_state, error, job_id, chunk_index),
        )
        self._db.execute(
            "UPDATE historical_jobs SET state=?, failed_chunk=?, error=? WHERE job_id=?",
            (next_state, chunk_index, error, job_id),
        )
        self._db.commit()

    def finish(self, job_id: str) -> HistoricalJob:
        self.get(job_id)
        self._refresh_counts(job_id)
        pending = self.pending_indices(job_id)
        terminal_failures = self._db.execute(
            "SELECT 1 FROM historical_job_chunks WHERE job_id=? AND state IN ('failed','running') LIMIT 1",
            (job_id,),
        ).fetchone()
        if terminal_failures is not None:
            state = "failed"
        elif pending:
            state = "progress"
        else:
            state = "completed"
        self._db.execute("UPDATE historical_jobs SET state=? WHERE job_id=?", (state, job_id))
        self._db.commit()
        return self.get(job_id)

    def _refresh_counts(self, job_id: str) -> None:
        completed = int(self._db.execute("SELECT COUNT(*) FROM historical_job_chunks WHERE job_id=? AND state='completed'", (job_id,)).fetchone()[0])
        skipped = int(self._db.execute("SELECT COUNT(*) FROM historical_job_chunks WHERE job_id=? AND state='skipped'", (job_id,)).fetchone()[0])
        self._db.execute(
            "UPDATE historical_jobs SET completed_chunks=?, skipped_chunks=?, state='progress' WHERE job_id=?",
            (completed, skipped, job_id),
        )
        self._db.commit()
