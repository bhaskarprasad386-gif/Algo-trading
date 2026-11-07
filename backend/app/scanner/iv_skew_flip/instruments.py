"""IV-scanner-only fallback for the official Angel One instrument master.

The primary Angel Broking host can time out independently of Angel One's API.
Keep this fallback scoped to the opt-in IV scanner; do not alter other scanners.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import requests
from zoneinfo import ZoneInfo

from app.core.logger import app_logger

ALTERNATE_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
IST = ZoneInfo("Asia/Kolkata")


def _validate_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("Angel One alternate instrument master is empty or invalid")
    rows = [
        row for row in payload
        if isinstance(row, dict)
        and str(row.get("symbol", "")).strip()
        and str(row.get("exch_seg", "")).strip()
        and str(row.get("token", "")).strip()
    ]
    required = ("NIFTY", "BANKNIFTY")
    missing = [
        symbol for symbol in required
        if not any(
            str(row.get("exch_seg", "")).strip().upper() == "NFO"
            and str(row.get("instrumenttype", "")).strip().upper() == "OPTIDX"
            and str(row.get("name", "")).strip().upper() == symbol
            for row in rows
        )
    ]
    if missing:
        raise ValueError(
            f"alternate instrument master is missing NFO index options for {missing}"
        )
    return rows


def download_iv_instruments(instrument_master: Any) -> list[dict[str, Any]]:
    """Try the shared primary loader, then a validated official alternate host."""
    try:
        rows = instrument_master.download()
        if rows:
            return rows
        raise ValueError("primary instrument master returned no rows")
    except Exception as primary_error:
        app_logger.warning(
            "IV scanner primary instrument-master download failed; trying official alternate host: {}",
            primary_error,
        )

    response = requests.get(ALTERNATE_MASTER_URL, timeout=(5, 20))
    response.raise_for_status()
    rows = _validate_rows(response.json())

    # Publish only a validated, complete official snapshot through the existing
    # InstrumentMaster instance/cache interface. The data is shared source data,
    # not an IV-specific subset, and no other scanner code is modified.
    cls = instrument_master.__class__
    lock = getattr(cls, "_cache_lock", None)
    if lock is None:
        instrument_master.instruments = rows
        instrument_master._loaded = True
    else:
        with lock:
            instrument_master.instruments = rows
            instrument_master._loaded = True
            instrument_master._symbol_exchange_index = None
            if hasattr(cls, "_cached_instruments"):
                cls._cached_instruments = rows
            if hasattr(cls, "_cached_snapshot_date"):
                cls._cached_snapshot_date = datetime.now(IST).date().isoformat()

    app_logger.warning(
        "IV scanner loaded validated Angel One instrument master from alternate official host (rows={})",
        len(rows),
    )
    return rows


__all__ = ["download_iv_instruments"]
