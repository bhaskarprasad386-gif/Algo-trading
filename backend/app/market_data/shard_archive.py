"""Verified archive/restore for completed daily SQLite market-data shards.

The archive layer is deliberately provider-neutral: its root can be a local
filesystem, mounted NAS, or a later external-storage mount. It never deletes
the source shard and never archives the current IST trading day.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import os
import shutil
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ARCHIVE_VERSION = 1
MANIFEST_SUFFIX = ".manifest.json"


@dataclass(frozen=True)
class ShardArchiveManifest:
    archive_version: int
    trading_date: str
    filename: str
    size_bytes: int
    sha256: str
    archived_at: str
    integrity_check: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_check(path: Path) -> str:
    connection = sqlite3.connect(str(path), timeout=10)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        value = str(result[0]) if result else ""
        if value.lower() != "ok":
            raise ValueError(f"SQLite integrity_check failed: {value or 'no result'}")
        return value
    finally:
        connection.close()


def _backup_sqlite(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(str(source), timeout=10)
    destination_connection = sqlite3.connect(str(destination), timeout=10)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _manifest_path(archive_path: Path) -> Path:
    return archive_path.with_name(archive_path.name + MANIFEST_SUFFIX)


def archive_completed_shard(
    shard_path: str | Path,
    archive_root: str | Path,
    trading_date: date,
    *,
    now: datetime | None = None,
) -> ShardArchiveManifest:
    """Archive one completed daily shard without modifying or deleting its source."""
    source = Path(shard_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    current_date = (now or datetime.now(IST)).astimezone(IST).date()
    if trading_date >= current_date:
        raise ValueError("current or future trading-day shard cannot be archived")

    archive_dir = Path(archive_root) / f"{trading_date:%Y_%m_%d}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    final_path = archive_dir / source.name
    manifest_path = _manifest_path(final_path)

    if final_path.exists() or manifest_path.exists():
        if not (final_path.exists() and manifest_path.exists()):
            raise FileExistsError("incomplete existing archive requires manual reconciliation")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("sha256") == _sha256(final_path):
            return ShardArchiveManifest(**existing)
        raise FileExistsError("archive exists with a different checksum")

    fd, temp_name = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".tmp", dir=str(archive_dir))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        _backup_sqlite(source, temp_path)
        integrity = _integrity_check(temp_path)
        checksum = _sha256(temp_path)
        size = temp_path.stat().st_size

        manifest = ShardArchiveManifest(
            archive_version=ARCHIVE_VERSION,
            trading_date=trading_date.isoformat(),
            filename=source.name,
            size_bytes=size,
            sha256=checksum,
            archived_at=(now or datetime.now(IST)).astimezone(IST).isoformat(),
            integrity_check=integrity,
        )

        os.replace(temp_path, final_path)
        manifest_path.write_text(
            json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest
    finally:
        temp_path.unlink(missing_ok=True)


def restore_archived_shard(
    archive_path: str | Path,
    destination: str | Path,
) -> Path:
    """Verify and restore an archived shard atomically; never overwrites a destination."""
    archive = Path(archive_path)
    manifest_file = _manifest_path(archive)
    if not archive.is_file() or not manifest_file.is_file():
        raise FileNotFoundError("archive or manifest is missing")

    manifest = ShardArchiveManifest(**json.loads(manifest_file.read_text(encoding="utf-8")))
    if manifest.filename != archive.name:
        raise ValueError("manifest filename does not match archive")
    if manifest.size_bytes != archive.stat().st_size:
        raise ValueError("archive size does not match manifest")
    if manifest.sha256 != _sha256(archive):
        raise ValueError("archive checksum does not match manifest")
    _integrity_check(archive)

    target = Path(destination)
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".restore", dir=str(target.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        _backup_sqlite(archive, temp_path)
        _integrity_check(temp_path)
        os.replace(temp_path, target)
        return target
    finally:
        temp_path.unlink(missing_ok=True)
