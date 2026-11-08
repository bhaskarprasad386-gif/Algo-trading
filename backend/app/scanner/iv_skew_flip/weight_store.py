"""Durable, append-only storage for validated IV constituent baskets.

This module intentionally has no HTTP route and never changes runtime switches.
A separate authenticated activation workflow must be added only after the app's
authorization model is established.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time, timezone
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.iv_constituent_basket_version import IVConstituentBasketVersion
from app.scanner.iv_skew_flip.weight_import import (
    VerifiedBasketImport,
    validate_basket_token_mapping,
)


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _require_https_source(name: str, url: str, label: str) -> tuple[str, str]:
    clean_name = str(name or "").strip()
    clean_url = str(url or "").strip()
    parsed = urlparse(clean_url)
    if not clean_name or parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label} name and HTTPS URL are required")
    return clean_name, parsed.geturl()


def persist_validated_basket_version(
    db: Session,
    basket: VerifiedBasketImport,
    symbol_to_token: dict[str, object],
    *,
    roster_source_name: str,
    roster_source_url: str,
    roster_sha256: str,
    created_by_user_id: int | None = None,
) -> IVConstituentBasketVersion:
    """Append a validated basket and full roster/token provenance as VALID only.

    The source file hash is unique per underlying, making retries safe. This
    function does not activate the record or toggle weighted-pressure runtime.
    """
    if not isinstance(basket, VerifiedBasketImport):
        raise ValueError("a validated basket import is required")
    if not _SHA256_RE.fullmatch(str(basket.sha256 or "")):
        raise ValueError("validated basket source SHA-256 is invalid")
    if not _SHA256_RE.fullmatch(str(roster_sha256 or "").lower()):
        raise ValueError("authoritative roster SHA-256 is required")
    roster_name, roster_url = _require_https_source(
        roster_source_name, roster_source_url, "roster source"
    )
    token_mapping = validate_basket_token_mapping(basket, symbol_to_token)
    source_name, source_url = _require_https_source(
        basket.source_name, basket.source_url, "basket source"
    )
    effective_at = datetime.combine(basket.effective_date, time.min, tzinfo=timezone.utc)
    record = IVConstituentBasketVersion(
        underlying=basket.underlying,
        effective_date=effective_at,
        source_name=source_name,
        source_url=source_url,
        source_sha256=basket.sha256,
        file_format=basket.file_format,
        weight_sum=float(basket.weight_sum),
        components_json=json.dumps(
            [{"symbol": item.symbol, "weight": float(item.weight)} for item in basket.components],
            sort_keys=True,
            separators=(",", ":"),
        ),
        token_mapping_json=json.dumps(token_mapping, sort_keys=True, separators=(",", ":")),
        roster_source_name=roster_name,
        roster_source_url=roster_url,
        roster_sha256=str(roster_sha256).lower(),
        status="VALID",
        created_by_user_id=created_by_user_id,
    )
    db.add(record)
    try:
        db.flush()
        db.commit()
        db.refresh(record)
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("this exact source file was already imported for the underlying") from exc
    except Exception:
        db.rollback()
        raise
    return record
