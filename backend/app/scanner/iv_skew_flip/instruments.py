"""IV-scanner-only resilient Angel One instrument-master loading.

The IV snapshot is independently stored and integrity-checked. This module
never reads or writes the Box Spread cache or shared InstrumentMaster cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.core.logger import app_logger


ALTERNATE_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
MAX_CACHE_AGE_SECONDS = 36 * 60 * 60
CACHE_SCHEMA_VERSION = 2


def _cache_path() -> Path:
    configured = os.environ.get("IV_INSTRUMENT_MASTER_CACHE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / "data" / "iv_instrument_master_cache.json"


def _canonical_rows_digest(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("Angel One instrument master is empty or invalid")
    # Do not silently discard malformed entries: a truncated/corrupt master
    # must not be promoted to a valid cache or used for token resolution.
    if any(
        not isinstance(row, dict)
        or not all(str(row.get(key, "")).strip() for key in ("symbol", "exch_seg", "token"))
        for row in payload
    ):
        raise ValueError("Angel One instrument master contains malformed or incomplete rows")

    rows = payload
    missing = []
    for underlying in ("NIFTY", "BANKNIFTY"):
        options = [
            row for row in rows
            if str(row.get("exch_seg", "")).strip().upper() == "NFO"
            and str(row.get("instrumenttype", "")).strip().upper() == "OPTIDX"
            and str(row.get("name", "")).strip().upper() == underlying
            and all(str(row.get(key, "")).strip() for key in ("expiry", "strike", "lotsize"))
        ]
        indices = [
            row for row in rows
            if str(row.get("exch_seg", "")).strip().upper() == "NSE"
            and str(row.get("token", "")).strip().startswith("999")
            and str(row.get("instrumenttype", "")).strip().upper() in {"", "AMXIDX"}
            and (
                str(row.get("symbol", "")).strip().upper() == underlying
                or str(row.get("name", "")).strip().upper() == underlying
            )
        ]
        if not options or len(indices) != 1:
            missing.append(underlying)

    if missing:
        raise ValueError(
            f"Angel One instrument master is missing complete NFO options or a unique NSE index token for {missing}"
        )
    # Ensure the exact payload can be hashed deterministically before use.
    _canonical_rows_digest(rows)
    return rows


def _write_snapshot(rows: list[dict[str, Any]]) -> None:
    path = _cache_path()
    temp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump({
                "schema_version": CACHE_SCHEMA_VERSION,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_sha256": _canonical_rows_digest(rows),
                "instruments": rows,
            }, handle, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except (OSError, TypeError, ValueError) as exc:
        app_logger.warning("IV scanner could not persist its isolated instrument snapshot: {}", exc)
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _read_recent_snapshot() -> tuple[list[dict[str, Any]], datetime] | None:
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_SCHEMA_VERSION:
            return None
        rows = _validate_rows(payload.get("instruments"))
        expected_digest = str(payload.get("content_sha256", "")).lower()
        if len(expected_digest) != 64 or expected_digest != _canonical_rows_digest(rows):
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


def _install_snapshot(instrument_master: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Install fallback rows on this IV-owned instance only."""
    instrument_master.instruments = rows
    instrument_master._loaded = True
    instrument_master._symbol_exchange_index = None
    return rows


def download_iv_instruments(instrument_master: Any) -> list[dict[str, Any]]:
    """Try primary host, official alternate host, then a recent integrity-checked IV snapshot."""
    primary_error: Exception | None = None
    try:
        rows = _validate_rows(instrument_master.download())
        _write_snapshot(rows)
        return rows
    except Exception as exc:
        primary_error = exc
        app_logger.warning(
            "IV scanner primary instrument-master download failed; trying official alternate host: {}",
            primary_error,
        )

    try:
        response = requests.get(ALTERNATE_MASTER_URL, timeout=(5, 20))
        response.raise_for_status()
        rows = _validate_rows(response.json())
        installed = _install_snapshot(instrument_master, rows)
        _write_snapshot(installed)
        app_logger.warning(
            "IV scanner loaded validated Angel One instrument master from alternate official host (rows={})",
            len(installed),
        )
        return installed
    except Exception as alternate_error:
        cached = _read_recent_snapshot()
        if cached is None:
            app_logger.error(
                "IV instrument master unavailable: primary host failed ({}); "
                "alternate host failed ({}); no recent integrity-checked IV snapshot exists",
                primary_error or "unknown primary instrument-master error",
                alternate_error,
            )
            primary_detail = f"{type(primary_error).__name__}: {primary_error}" if primary_error else "unknown"
            alternate_detail = f"{type(alternate_error).__name__}: {alternate_error}"
            raise RuntimeError(
                "IV instrument master unavailable; "
                f"primary Angel One source failed [{primary_detail}]; "
                f"alternate Angel One source failed [{alternate_detail}]; "
                "no recent integrity-checked IV snapshot (schema v2, max age 36h) is available"
            ) from alternate_error

        rows, fetched_at = cached
        installed = _install_snapshot(instrument_master, rows)
        app_logger.warning(
            "IV scanner using read-only integrity-checked IV instrument snapshot (fetched_at={}, rows={})",
            fetched_at.isoformat(),
            len(installed),
        )
        return installed


__all__ = ["download_iv_instruments"]
