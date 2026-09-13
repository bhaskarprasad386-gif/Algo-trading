"""Durable SQLite status for resumable historical-download jobs and chunks."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DownloadJobStatus:
    job_id: str
    source: str
    mode: str
    timeframe: str
    spot_instrument: str
    exchange: str
    underlying: str
    start_ns: int
    end_ns: int
    status: str
    requested_chunks: int
    completed_chunks: int
    skipped_chunks: int
    failed_chunks: int
    catalog_count: int
    fetched_records: int
    inserted_records: int
    updated_at_ns: int
    error: str | None = None


@dataclass(frozen=True)
class DownloadChunkStatus:
    job_id: str
    sequence: int
    instrument: str
    start_ns: int
    end_ns: int
    status: str
    attempts: int
    expected_timestamps: int = 0
    actual_timestamps: int = 0
    missing_timestamps: int = 0
    first_missing_ns: int | None = None
    fetched_records: int = 0
    inserted_records: int = 0
    error: str | None = None
    updated_at_ns: int = 0


class HistoricalDownloadStatusStore:
    """Transactional status store; market data itself is never loaded here."""

    VALID_JOB_STATUSES = frozenset({"QUEUED", "RUNNING", "COMPLETE", "FAILED"})
    VALID_CHUNK_STATUSES = frozenset({"QUEUED", "RUNNING", "SKIPPED", "COMPLETE", "COMPLETED", "FAILED"})

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("""CREATE TABLE IF NOT EXISTS download_jobs (
            job_id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'angelone', mode TEXT NOT NULL, timeframe TEXT NOT NULL,
            spot_instrument TEXT NOT NULL, exchange TEXT NOT NULL, underlying TEXT NOT NULL,
            start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, status TEXT NOT NULL,
            requested_chunks INTEGER NOT NULL DEFAULT 0, completed_chunks INTEGER NOT NULL DEFAULT 0,
            skipped_chunks INTEGER NOT NULL DEFAULT 0, failed_chunks INTEGER NOT NULL DEFAULT 0,
            catalog_count INTEGER NOT NULL DEFAULT 0, fetched_records INTEGER NOT NULL DEFAULT 0,
            inserted_records INTEGER NOT NULL DEFAULT 0, updated_at_ns INTEGER NOT NULL, error TEXT
        )""")
        self._ensure_job_schema()
        self._ensure_chunk_schema()
        self._db.commit()

    def _ensure_job_schema(self) -> None:
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(download_jobs)").fetchall()}
        if "source" not in cols:
            self._db.execute("ALTER TABLE download_jobs ADD COLUMN source TEXT NOT NULL DEFAULT 'angelone'")
        if "fetched_records" not in cols:
            self._db.execute("ALTER TABLE download_jobs ADD COLUMN fetched_records INTEGER NOT NULL DEFAULT 0")
        if "inserted_records" not in cols:
            self._db.execute("ALTER TABLE download_jobs ADD COLUMN inserted_records INTEGER NOT NULL DEFAULT 0")

    def _ensure_chunk_schema(self) -> None:
        self._db.execute("""CREATE TABLE IF NOT EXISTS download_chunks (
            job_id TEXT NOT NULL, sequence INTEGER NOT NULL, instrument TEXT NOT NULL,
            start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, expected_timestamps INTEGER NOT NULL DEFAULT 0,
            actual_timestamps INTEGER NOT NULL DEFAULT 0, missing_timestamps INTEGER NOT NULL DEFAULT 0,
            first_missing_ns INTEGER, fetched_records INTEGER NOT NULL DEFAULT 0,
            inserted_records INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at_ns INTEGER NOT NULL,
            PRIMARY KEY(job_id, sequence, instrument), FOREIGN KEY(job_id) REFERENCES download_jobs(job_id) ON DELETE CASCADE
        )""")
        cols = self._db.execute("PRAGMA table_info(download_chunks)").fetchall()
        pk_cols = {row[1] for row in cols if row[5]}
        if pk_cols == {"job_id", "sequence"}:
            self._db.execute("ALTER TABLE download_chunks RENAME TO download_chunks_legacy")
            self._db.execute("""CREATE TABLE download_chunks (
                job_id TEXT NOT NULL, sequence INTEGER NOT NULL, instrument TEXT NOT NULL,
                start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, expected_timestamps INTEGER NOT NULL DEFAULT 0,
                actual_timestamps INTEGER NOT NULL DEFAULT 0, missing_timestamps INTEGER NOT NULL DEFAULT 0,
                first_missing_ns INTEGER, fetched_records INTEGER NOT NULL DEFAULT 0,
                inserted_records INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at_ns INTEGER NOT NULL,
                PRIMARY KEY(job_id, sequence, instrument), FOREIGN KEY(job_id) REFERENCES download_jobs(job_id) ON DELETE CASCADE
            )""")
            self._db.execute("""INSERT INTO download_chunks(job_id,sequence,instrument,start_ns,end_ns,status,attempts,expected_timestamps,actual_timestamps,missing_timestamps,first_missing_ns,error,updated_at_ns)
                SELECT job_id,sequence,instrument,start_ns,end_ns,status,attempts,expected_timestamps,actual_timestamps,missing_timestamps,first_missing_ns,error,updated_at_ns FROM download_chunks_legacy""")
            self._db.execute("DROP TABLE download_chunks_legacy")
            return
        names = {row[1] for row in cols}
        if "fetched_records" not in names:
            self._db.execute("ALTER TABLE download_chunks ADD COLUMN fetched_records INTEGER NOT NULL DEFAULT 0")
        if "inserted_records" not in names:
            self._db.execute("ALTER TABLE download_chunks ADD COLUMN inserted_records INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _now_ns() -> int:
        return time.time_ns()

    def create_job(self, *, job_id: str, source: str = "angelone", mode: str, timeframe: str, spot_instrument: str,
                   exchange: str, underlying: str, start_ns: int, end_ns: int,
                   requested_chunks: int = 0, status: str = "QUEUED",
                   updated_at_ns: int | None = None, reset_existing: bool = True) -> None:
        with self._lock:
            if not job_id.strip() or not source.strip():
                raise ValueError("job_id and source are required")
            if status not in self.VALID_JOB_STATUSES:
                raise ValueError(f"invalid job status: {status}")
            if start_ns < 0 or end_ns < start_ns or requested_chunks < 0:
                raise ValueError("invalid job range or chunk count")
            existing = self.job(job_id)
            if existing is not None:
                if not reset_existing:
                    self.update_job(job_id, source=source, requested_chunks=requested_chunks, status=status, error=None,
                                    updated_at_ns=updated_at_ns or self._now_ns())
                    return
                self._db.execute("DELETE FROM download_chunks WHERE job_id=?", (job_id,))
                self.update_job(job_id, source=source, status=status, requested_chunks=requested_chunks,
                                completed_chunks=0, skipped_chunks=0, failed_chunks=0, catalog_count=0,
                                fetched_records=0, inserted_records=0, error=None,
                                updated_at_ns=updated_at_ns or self._now_ns())
                return
            self._db.execute(
                "INSERT INTO download_jobs(job_id,source,mode,timeframe,spot_instrument,exchange,underlying,start_ns,end_ns,status,requested_chunks,fetched_records,inserted_records,updated_at_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, source, mode, timeframe, spot_instrument, exchange, underlying, start_ns, end_ns, status,
                 requested_chunks, 0, 0, updated_at_ns or self._now_ns()),
            )
            self._db.commit()

    def update_job(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            allowed = {"source", "status", "requested_chunks", "completed_chunks", "skipped_chunks", "failed_chunks",
                       "catalog_count", "fetched_records", "inserted_records", "error", "updated_at_ns"}
            unknown = set(fields) - allowed
            if unknown:
                raise ValueError(f"unsupported job fields: {sorted(unknown)}")
            if "source" in fields and not str(fields["source"]).strip():
                raise ValueError("source is required")
            if "status" in fields and fields["status"] not in self.VALID_JOB_STATUSES:
                raise ValueError(f"invalid job status: {fields['status']}")
            if not fields:
                return
            fields.setdefault("updated_at_ns", self._now_ns())
            assignments = ", ".join(f"{key}=?" for key in fields)
            values = [fields[key] for key in fields] + [job_id]
            cursor = self._db.execute(f"UPDATE download_jobs SET {assignments} WHERE job_id=?", values)
            if cursor.rowcount != 1:
                raise KeyError(job_id)
            self._db.commit()

    def upsert_chunk(self, chunk: DownloadChunkStatus) -> None:
        with self._lock:
            if chunk.status not in self.VALID_CHUNK_STATUSES:
                raise ValueError(f"invalid chunk status: {chunk.status}")
            if chunk.sequence < 0 or chunk.start_ns < 0 or chunk.end_ns < chunk.start_ns:
                raise ValueError("invalid chunk range")
            self._db.execute("""INSERT INTO download_chunks(job_id,sequence,instrument,start_ns,end_ns,status,attempts,expected_timestamps,actual_timestamps,missing_timestamps,first_missing_ns,fetched_records,inserted_records,error,updated_at_ns)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,sequence,instrument) DO UPDATE SET start_ns=excluded.start_ns,end_ns=excluded.end_ns,status=excluded.status,attempts=excluded.attempts,expected_timestamps=excluded.expected_timestamps,actual_timestamps=excluded.actual_timestamps,missing_timestamps=excluded.missing_timestamps,first_missing_ns=excluded.first_missing_ns,fetched_records=excluded.fetched_records,inserted_records=excluded.inserted_records,error=excluded.error,updated_at_ns=excluded.updated_at_ns""",
                (chunk.job_id, chunk.sequence, chunk.instrument, chunk.start_ns, chunk.end_ns, chunk.status, chunk.attempts,
                 chunk.expected_timestamps, chunk.actual_timestamps, chunk.missing_timestamps, chunk.first_missing_ns,
                 chunk.fetched_records, chunk.inserted_records, chunk.error, chunk.updated_at_ns or self._now_ns()))
            self._db.commit()

    def job(self, job_id: str) -> DownloadJobStatus | None:
        with self._lock:
            row = self._db.execute("SELECT job_id,source,mode,timeframe,spot_instrument,exchange,underlying,start_ns,end_ns,status,requested_chunks,completed_chunks,skipped_chunks,failed_chunks,catalog_count,fetched_records,inserted_records,updated_at_ns,error FROM download_jobs WHERE job_id=?", (job_id,)).fetchone()
            return DownloadJobStatus(*row) if row else None

    def chunks(self, job_id: str) -> tuple[DownloadChunkStatus, ...]:
        with self._lock:
            rows = self._db.execute("SELECT job_id,sequence,instrument,start_ns,end_ns,status,attempts,expected_timestamps,actual_timestamps,missing_timestamps,first_missing_ns,fetched_records,inserted_records,error,updated_at_ns FROM download_chunks WHERE job_id=? ORDER BY sequence,instrument", (job_id,)).fetchall()
            return tuple(DownloadChunkStatus(*row) for row in rows)

    def incomplete_chunks(self, job_id: str) -> tuple[DownloadChunkStatus, ...]:
        """Return only chunks that must be replayed, preserving their exact identity/range."""
        with self._lock:
            rows = self._db.execute(
                """SELECT job_id,sequence,instrument,start_ns,end_ns,status,attempts,
                          expected_timestamps,actual_timestamps,missing_timestamps,
                          first_missing_ns,fetched_records,inserted_records,error,updated_at_ns
                   FROM download_chunks
                   WHERE job_id=? AND (status NOT IN ('COMPLETE','SKIPPED') OR missing_timestamps > 0)
                   ORDER BY sequence,instrument""",
                (job_id,),
            ).fetchall()
            return tuple(DownloadChunkStatus(*row) for row in rows)
