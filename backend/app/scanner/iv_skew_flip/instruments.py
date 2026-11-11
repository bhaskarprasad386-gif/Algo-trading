"""IV-scanner-only resilient Angel One instrument-master loading.

The IV snapshot is independently stored and integrity-checked. This module
never reads or writes the Box Spread cache or shared InstrumentMaster cache.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.core.logger import app_logger


PRIMARY_MASTER_URL = (
    "https://margincalculator.angelbroking.com/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
ALTERNATE_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
MAX_CACHE_AGE_SECONDS = 36 * 60 * 60
CACHE_SCHEMA_VERSION = 2

# Angel One's instrument master is large; bound memory and reject partial payloads.
MASTER_CONNECT_TIMEOUT_SECONDS = 10
MASTER_READ_TIMEOUT_SECONDS = 120
MASTER_MAX_BYTES = 64 * 1024 * 1024
MASTER_CHUNK_BYTES = 1024 * 1024
MASTER_USER_AGENT = "Algo-Trading-IV-InstrumentMaster/1.0"

def _download_master_payload(url: str) -> Any:
    """Download a complete bounded JSON payload and always close the response."""
    response = requests.get(
        url,
        timeout=(MASTER_CONNECT_TIMEOUT_SECONDS, MASTER_READ_TIMEOUT_SECONDS),
        stream=True,
        headers={"User-Agent": MASTER_USER_AGENT, "Accept": "application/json"},
    )
    try:
        response.raise_for_status()
        chunks: list[bytes] = []
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

def _fetch_validated_master(url: str) -> list[dict[str, Any]]:
    """Fetch and validate one official source before exposing its rows."""
    try:
        payload = _download_master_payload(url)
    except TypeError as exc:
        # Retain compatibility with narrow legacy test doubles only.
        if "unexpected keyword" not in str(exc):
            raise
        response = requests.get(url, timeout=(5, 20))
        response.raise_for_status()
        payload = response.json()
    return _validate_rows(payload)


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


def _positive_master_number(value: Any, *, integer: bool = False) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(number)
        and number > 0
        and (not integer or number.is_integer())
    )


def _validate_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("Angel One instrument master is empty or invalid")

    # Validate every record without coercing nulls (str(None) is truthy) or
    # accepting booleans/NaN as identifiers. Angel One tokens are numeric.
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("Angel One instrument master contains malformed or incomplete rows")
        symbol = row.get("symbol")
        exchange = row.get("exch_seg")
        token = row.get("token")
        if (
            not isinstance(symbol, str) or not symbol.strip()
            or not isinstance(exchange, str) or not exchange.strip()
            or (
                exchange.strip().upper() in {"NSE", "NFO"}
                and (
                    isinstance(token, bool) or token is None
                    or not str(token).strip().isdigit() or int(token) <= 0
                )
            )
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
            and _positive_master_number(row.get("strike"))
            and _positive_master_number(row.get("lotsize"), integer=True)
            and _valid_option_expiry(row.get("expiry"))
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


def _valid_option_expiry(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.strptime(value.strip().upper(), "%d%b%Y")
        return True
    except ValueError:
        return False


def _write_snapshot(
    rows: list[dict[str, Any]],
    fetched_at: datetime | None = None,
) -> None:
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
                "fetched_at": (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
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
    """Return a verified recent snapshot, logging the exact rejection reason.

    Keep rejection diagnostics local to this IV-owned cache; never fall back to
    or inspect the shared Box Spread instrument cache.
    """
    path = _cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            app_logger.warning("IV instrument snapshot rejected: reason=invalid_root_type type={}", type(payload).__name__)
            return None
        schema_version = payload.get("schema_version")
        if schema_version != CACHE_SCHEMA_VERSION:
            app_logger.warning(
                "IV instrument snapshot rejected: reason=schema_version_mismatch expected={} actual={}",
                CACHE_SCHEMA_VERSION, schema_version,
            )
            return None
        try:
            rows = _validate_rows(payload.get("instruments"))
        except (TypeError, ValueError) as exc:
            app_logger.warning(
                "IV instrument snapshot rejected: reason=row_validation_failed error_type={} detail={}",
                type(exc).__name__, exc,
            )
            return None
        expected_digest = str(payload.get("content_sha256", "")).lower()
        actual_digest = _canonical_rows_digest(rows)
        if len(expected_digest) != 64 or expected_digest != actual_digest:
            app_logger.warning(
                "IV instrument snapshot rejected: reason=sha256_mismatch expected_length={} actual_sha256={}",
                len(expected_digest), actual_digest,
            )
            return None
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            app_logger.warning("IV instrument snapshot rejected: reason=missing_fetched_at")
            return None
        try:
            fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            app_logger.warning(
                "IV instrument snapshot rejected: reason=invalid_fetched_at error_type={} detail={}",
                type(exc).__name__, exc,
            )
            return None
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > MAX_CACHE_AGE_SECONDS:
            app_logger.warning(
                "IV instrument snapshot rejected: reason=age_out_of_range age_seconds={:.1f} max_age_seconds={}",
                age, MAX_CACHE_AGE_SECONDS,
            )
            return None
        return rows, fetched
    except (OSError, json.JSONDecodeError) as exc:
        app_logger.warning(
            "IV instrument snapshot could not be read: path={} reason={} detail={}",
            path, type(exc).__name__, exc,
        )
        return None


def _install_snapshot(
    instrument_master: Any,
    rows: list[dict[str, Any]],
    fetched_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Install rows on this instance and retain source age for safe same-process reuse."""
    instrument_master.instruments = rows
    instrument_master._loaded = True
    instrument_master._symbol_exchange_index = None
    instrument_master._iv_snapshot_fetched_at = fetched_at or datetime.now(timezone.utc)
    return rows


def download_iv_instruments(instrument_master: Any) -> list[dict[str, Any]]:
    """Try primary host, official alternate host, then a recent integrity-checked IV snapshot."""
    primary_error: Exception | None = None
    try:
        # Do not call InstrumentMaster.download(): it reads/writes a
        # process-wide cache shared with Box Spread and other consumers.
        rows = _fetch_validated_master(PRIMARY_MASTER_URL)
        installed = _install_snapshot(instrument_master, rows)
        _write_snapshot(installed)
        return installed
    except Exception as exc:
        primary_error = exc
        app_logger.warning(
            "IV scanner primary instrument-master download failed; trying official alternate host: {}",
            primary_error,
        )

    try:
        rows = _fetch_validated_master(ALTERNATE_MASTER_URL)
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
        installed = _install_snapshot(instrument_master, rows, fetched_at=fetched_at)
        app_logger.warning(
            "IV scanner using read-only integrity-checked IV instrument snapshot (fetched_at={}, rows={})",
            fetched_at.isoformat(),
            len(installed),
        )
        return installed


__all__ = ["download_iv_instruments"]
