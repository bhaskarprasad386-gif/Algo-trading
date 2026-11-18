"""Read-only, fail-closed status projection for audited IV index baskets."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime

from app.scanner.iv_skew_flip.weight_import import WEIGHT_SUM_TOLERANCE, is_official_nse_source_url

SUPPORTED_UNDERLYINGS = ("NIFTY", "BANKNIFTY")
MAX_BASKET_AGE_DAYS = 45
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _valid_record(record, as_of: date) -> tuple[bool, str, dict]:
    effective = getattr(record, "effective_date", None)
    if not isinstance(effective, (date, datetime)):
        return False, "INVALID_EFFECTIVE_DATE", {}
    effective_day = effective.date() if isinstance(effective, datetime) else effective
    age_days = (as_of - effective_day).days
    if age_days < 0:
        return False, "FUTURE_DATED", {"effective_date": effective_day.isoformat(), "age_days": age_days}
    if age_days > MAX_BASKET_AGE_DAYS:
        return False, "STALE", {"effective_date": effective_day.isoformat(), "age_days": age_days}
    if str(getattr(record, "status", "")).upper() != "VALID":
        return False, "REVOKED_OR_INVALID", {"effective_date": effective_day.isoformat(), "age_days": age_days}

    source_url = str(getattr(record, "source_url", "") or "")
    roster_url = str(getattr(record, "roster_source_url", "") or "")
    if not is_official_nse_source_url(source_url) or not is_official_nse_source_url(roster_url):
        return False, "INVALID_SOURCE_PROVENANCE", {"effective_date": effective_day.isoformat(), "age_days": age_days}
    if not _SHA256_RE.fullmatch(str(getattr(record, "source_sha256", "") or "")):
        return False, "INVALID_SOURCE_HASH", {"effective_date": effective_day.isoformat(), "age_days": age_days}
    if not _SHA256_RE.fullmatch(str(getattr(record, "roster_sha256", "") or "")):
        return False, "INVALID_ROSTER_HASH", {"effective_date": effective_day.isoformat(), "age_days": age_days}

    try:
        components = json.loads(record.components_json)
        tokens = json.loads(record.token_mapping_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False, "INVALID_STORED_PAYLOAD", {"effective_date": effective_day.isoformat(), "age_days": age_days}
    if not isinstance(components, list) or not components or not isinstance(tokens, dict):
        return False, "INVALID_STORED_PAYLOAD", {"effective_date": effective_day.isoformat(), "age_days": age_days}

    symbols = []
    weights = []
    for item in components:
        if not isinstance(item, dict):
            return False, "INVALID_STORED_COMPONENT", {"effective_date": effective_day.isoformat(), "age_days": age_days}
        symbol = item.get("symbol")
        weight = item.get("weight")
        if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip().upper() or symbol in symbols:
            return False, "INVALID_STORED_COMPONENT", {"effective_date": effective_day.isoformat(), "age_days": age_days}
        if isinstance(weight, bool):
            return False, "INVALID_STORED_COMPONENT", {"effective_date": effective_day.isoformat(), "age_days": age_days}
        try:
            numeric_weight = float(weight)
        except (TypeError, ValueError, OverflowError):
            return False, "INVALID_STORED_COMPONENT", {"effective_date": effective_day.isoformat(), "age_days": age_days}
        if not math.isfinite(numeric_weight) or numeric_weight <= 0:
            return False, "INVALID_STORED_COMPONENT", {"effective_date": effective_day.isoformat(), "age_days": age_days}
        symbols.append(symbol)
        weights.append(numeric_weight)

    total = math.fsum(weights)
    try:
        declared_total = float(record.weight_sum)
    except (TypeError, ValueError, OverflowError):
        declared_total = math.nan
    if (
        not math.isfinite(total)
        or not math.isfinite(declared_total)
        or abs(total - 100.0) > WEIGHT_SUM_TOLERANCE
        or abs(declared_total - total) > 1e-9
    ):
        return False, "INVALID_WEIGHT_TOTAL", {"effective_date": effective_day.isoformat(), "age_days": age_days}
    if set(tokens) != set(symbols) or len(tokens) != len(symbols):
        return False, "INCOMPLETE_TOKEN_MAPPING", {"effective_date": effective_day.isoformat(), "age_days": age_days}
    for token in tokens.values():
        if not isinstance(token, str) or not re.fullmatch(r"[1-9][0-9]*", token):
            return False, "INVALID_TOKEN_MAPPING", {"effective_date": effective_day.isoformat(), "age_days": age_days}

    return True, "VERIFIED_FRESH", {
        "effective_date": effective_day.isoformat(),
        "age_days": age_days,
        "constituent_count": len(symbols),
        "mapped_token_count": len(tokens),
        "weight_sum": round(total, 8),
        "symbols": sorted(symbols),
        "source_name": record.source_name,
        "source_url": source_url,
        "source_sha256": record.source_sha256,
        "roster_source_name": record.roster_source_name,
        "roster_source_url": roster_url,
        "roster_sha256": record.roster_sha256,
        "imported_at": record.created_at.isoformat() if getattr(record, "created_at", None) else None,
    }


def summarize_basket_registry(records, *, as_of: date | None = None) -> dict:
    """Return the latest immutable record per supported index; never activates it."""
    today = as_of or date.today()
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("as_of must be a calendar date")
    latest = {}
    for record in records:
        underlying = str(getattr(record, "underlying", "") or "").strip().upper()
        if underlying not in SUPPORTED_UNDERLYINGS:
            continue
        effective = getattr(record, "effective_date", None)
        created = getattr(record, "created_at", None)
        key = (
            effective.isoformat() if hasattr(effective, "isoformat") else "",
            created.isoformat() if hasattr(created, "isoformat") else "",
            int(getattr(record, "id", 0) or 0),
        )
        if underlying not in latest or key > latest[underlying][0]:
            latest[underlying] = (key, record)

    baskets = {}
    for underlying in SUPPORTED_UNDERLYINGS:
        if underlying not in latest:
            baskets[underlying] = {
                "status": "MISSING",
                "signals_allowed": False,
                "reason": "NO_IMPORTED_OFFICIAL_BASKET",
            }
            continue
        record = latest[underlying][1]
        valid, status, details = _valid_record(record, today)
        baskets[underlying] = {
            "status": status,
            "signals_allowed": False,
            "record_id": getattr(record, "id", None),
            **details,
        }
        if not valid:
            baskets[underlying]["reason"] = status
    return {
        "status": "ok",
        "as_of": today.isoformat(),
        "max_age_days": MAX_BASKET_AGE_DAYS,
        "baskets": baskets,
        "activation_enabled": False,
        "signals_allowed": False,
        "note": "Registry is read-only; a verified basket does not enable the IV pressure runner.",
    }
