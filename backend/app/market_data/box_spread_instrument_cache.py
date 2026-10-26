"""Box-Spread-only durable instrument-master fallback.

This deliberately leaves the shared InstrumentMaster implementation unchanged.
Only the Box Spread runner calls this helper.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.logger import app_logger


MAX_CACHE_AGE_SECONDS = 36 * 60 * 60


def _cache_path() -> Path:
    configured = os.environ.get("BOX_SPREAD_INSTRUMENT_MASTER_CACHE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "box_spread_instrument_master_cache.json"


def _validated_rows(rows):
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        return None
    usable = [
        row for row in rows
        if str(row.get("symbol", "")).strip()
        and str(row.get("exch_seg", "")).strip()
        and str(row.get("token", "")).strip()
    ]
    return usable or None


def _read_cache():
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        rows = _validated_rows(payload.get("instruments") if isinstance(payload, dict) else None)
        fetched_at = payload.get("fetched_at") if isinstance(payload, dict) else None
        if rows is None or not fetched_at:
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


def _write_cache(rows):
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
                "schema_version": 1,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "instruments": rows,
            }, handle, separators=(",", ":"))
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


def download_box_spread_instruments(master):
    """Refresh master normally; use only Box Spread's own recent cache on failure."""
    try:
        rows = master.download()
        _write_cache(rows)
        return rows
    except Exception:
        cached = _read_cache()
        if cached is None:
            raise
        rows, fetched = cached
        master.instruments = rows
        master._loaded = True
        master._symbol_exchange_index = None
        app_logger.warning(
            "Box Spread using its own validated instrument-master cache "
            "(fetched_at={}, rows={})", fetched.isoformat(), len(rows)
        )
        return rows
