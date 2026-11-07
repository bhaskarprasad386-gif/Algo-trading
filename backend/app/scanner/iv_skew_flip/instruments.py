"""IV-scanner-only resilient Angel One instrument-master loading.

The primary Angel Broking host can time out independently of Angel One's API.
The Box Spread durable snapshot is only read as a final fallback and is never
modified by this module. No other scanner lifecycle or cache writer is used.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.core.logger import app_logger


ALTERNATE_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
IST = ZoneInfo("Asia/Kolkata")
MAX_CACHE_AGE_SECONDS = 36 * 60 * 60


def _cache_path() -> Path:
    configured = os.environ.get("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / "data" / "box_spread_instrument_master_cache.json"


def _validate_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("Angel One instrument master is empty or invalid")

    rows = [
        row for row in payload
        if isinstance(row, dict)
        and all(str(row.get(key, "")).strip() for key in ("symbol", "exch_seg", "token"))
    ]
    if not rows:
        raise ValueError("Angel One instrument master has no usable instrument rows")

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
    return rows


def _read_recent_snapshot() -> tuple[list[dict[str, Any]], datetime] | None:
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        rows = _validate_rows(payload.get("instruments"))
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
    cls = instrument_master.__class__
    lock = getattr(cls, "_cache_lock", None)

    def install() -> None:
        instrument_master.instruments = rows
        instrument_master._loaded = True
        instrument_master._symbol_exchange_index = None
        if hasattr(cls, "_cached_instruments"):
            cls._cached_instruments = rows
        if hasattr(cls, "_cached_snapshot_date"):
            cls._cached_snapshot_date = datetime.now(IST).date().isoformat()

    if lock is None:
        install()
    else:
        with lock:
            install()
    return rows


def download_iv_instruments(instrument_master: Any) -> list[dict[str, Any]]:
    """Try primary host, official alternate host, then a recent validated snapshot."""
    try:
        rows = _validate_rows(instrument_master.download())
        return rows
    except Exception as primary_error:
        app_logger.warning(
            "IV scanner primary instrument-master download failed; trying official alternate host: {}",
            primary_error,
        )

    try:
        response = requests.get(ALTERNATE_MASTER_URL, timeout=(5, 20))
        response.raise_for_status()
        rows = _validate_rows(response.json())
        installed = _install_snapshot(instrument_master, rows)
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
                "alternate host failed ({}); no recent validated snapshot exists",
                primary_error,
                alternate_error,
            )
            raise RuntimeError(
                "IV instrument master unavailable: primary and alternate Angel One hosts failed "
                "and no recent complete snapshot is available"
            ) from alternate_error

        rows, fetched_at = cached
        installed = _install_snapshot(instrument_master, rows)
        app_logger.warning(
            "IV scanner using read-only validated instrument snapshot (fetched_at={}, rows={})",
            fetched_at.isoformat(),
            len(installed),
        )
        return installed


__all__ = ["download_iv_instruments"]
