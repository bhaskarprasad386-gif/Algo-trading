"""Box-Spread-only durable instrument-master fallback and recovery cache.

This deliberately leaves the shared InstrumentMaster implementation unchanged.
Only the Box Spread runner calls this helper. Cache schema v2 includes a
canonical content digest and retains the original source timestamp.
"""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from app.core.logger import app_logger


CACHE_SCHEMA_VERSION = 2
MAX_CACHE_AGE_SECONDS = 36 * 60 * 60
ALTERNATE_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

MASTER_CONNECT_TIMEOUT_SECONDS = 10
MASTER_READ_TIMEOUT_SECONDS = 120
MASTER_MAX_BYTES = 64 * 1024 * 1024
MASTER_CHUNK_BYTES = 1024 * 1024


def _canonical_rows_digest(rows):
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _download_alternate_rows():
    """Stream the official alternate master with a strict size bound."""
    try:
        response = requests.get(
            ALTERNATE_MASTER_URL,
            timeout=(MASTER_CONNECT_TIMEOUT_SECONDS, MASTER_READ_TIMEOUT_SECONDS),
            stream=True,
            headers={"User-Agent": "Algo-Trading-BoxSpread-InstrumentMaster/1.0", "Accept": "application/json"},
        )
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        response = requests.get(ALTERNATE_MASTER_URL, timeout=20)
    try:
        response.raise_for_status()
        if not hasattr(response, "iter_content"):
            return response.json()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=MASTER_CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > MASTER_MAX_BYTES:
                raise ValueError(f"Angel One instrument master exceeded {MASTER_MAX_BYTES} byte limit")
            chunks.append(chunk)
        if not chunks:
            raise ValueError("Angel One instrument master response was empty")
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _cache_path() -> Path:
    configured = os.environ.get("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "box_spread_instrument_master_cache.json"


def _validated_rows(rows):
    if not isinstance(rows, list) or not rows:
        return None
    if not all(isinstance(row, dict) for row in rows):
        return None
    for row in rows:
        for field in ("symbol", "exch_seg", "token"):
            value = row.get(field)
            if value is None or not str(value).strip():
                return None
    return rows


def _read_cache():
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_SCHEMA_VERSION:
            return None
        rows = _validated_rows(payload.get("instruments"))
        if rows is None:
            return None
        expected_digest = str(payload.get("content_sha256", "")).lower()
        actual_digest = _canonical_rows_digest(rows)
        if len(expected_digest) != 64 or expected_digest != actual_digest:
            app_logger.warning("Box Spread cache rejected: content SHA-256 mismatch")
            return None
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            return None
        fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > MAX_CACHE_AGE_SECONDS:
            return None
        return rows, fetched
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(rows, fetched_at=None):
    rows = _validated_rows(rows)
    if rows is None:
        raise ValueError("refusing to persist invalid Box Spread instrument rows")
    path = _cache_path()
    temp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=path.name + ".", suffix=".tmp", delete=False
        ) as handle:
            temp_name = handle.name
            json.dump({
                "schema_version": CACHE_SCHEMA_VERSION,
                "fetched_at": (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                "content_sha256": _canonical_rows_digest(rows),
                "instruments": rows,
            }, handle, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        app_logger.warning("Box Spread could not persist instrument-master cache: {}", exc)
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _install_rows(master, rows):
    master.instruments = rows
    master._loaded = True
    master._symbol_exchange_index = None
    cls = master.__class__
    if hasattr(cls, "_cached_instruments"):
        cls._cached_instruments = rows
    if hasattr(cls, "_cached_snapshot_date"):
        cls._cached_snapshot_date = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    return rows


def _load_alternate_master(master):
    """Try Angel One's alternate public instrument-master host for Box Spread only."""
    payload = _download_alternate_rows()
    rows = _validated_rows(payload)
    if rows is None:
        raise ValueError("Angel One alternate instrument master returned invalid/empty rows")
    _install_rows(master, rows)
    _write_cache(rows)
    app_logger.warning(
        "Box Spread loaded instrument master from alternate Angel One host (rows={})",
        len(rows),
    )
    return rows


def download_box_spread_instruments(master):
    """Refresh normally; reuse a fresh verified process snapshot, then alternate host/disk cache."""
    source_fetched_at = getattr(master, "_iv_snapshot_fetched_at", None)
    if getattr(master, "_loaded", False) and source_fetched_at is not None:
        try:
            fetched = source_fetched_at
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
            rows = _validated_rows(getattr(master, "instruments", None))
            if rows is not None and 0 <= age <= MAX_CACHE_AGE_SECONDS:
                _write_cache(rows, fetched_at=fetched)
                app_logger.info("Box Spread reused validated in-process instrument snapshot (age_seconds={:.1f}, rows={})", age, len(rows))
                return rows
        except (TypeError, ValueError, OverflowError) as exc:
            app_logger.warning("Box Spread ignored in-process instrument snapshot: {}", exc)
    try:
        rows = _validated_rows(master.download())
        if rows is None:
            raise ValueError("primary Angel One instrument master returned invalid/empty rows")
        _write_cache(rows)
        return rows
    except Exception as primary_error:
        try:
            return _load_alternate_master(master)
        except Exception as alternate_error:
            cached = _read_cache()
            if cached is None:
                app_logger.error(
                    "Box Spread instrument master unavailable: primary host failed ({}); "
                    "alternate host failed ({}); no valid recent integrity-checked Box Spread cache exists",
                    primary_error, alternate_error,
                )
                raise primary_error
            rows, fetched = cached
            _install_rows(master, rows)
            app_logger.warning(
                "Box Spread using its own integrity-checked instrument-master cache "
                "(fetched_at={}, rows={})", fetched.isoformat(), len(rows)
            )
            return rows
