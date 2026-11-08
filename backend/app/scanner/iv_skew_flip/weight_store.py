""""Durable, append-only storage for validated IV constituent baskets.

This module intentionally has no HTTP route and never changes runtime switches.
A separate activation workflow must be added only after the application's
no-login control boundary has been verified.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.iv_constituent_basket_version import IVConstituentBasketVersion
from app.scanner.iv_skew_flip.weight_import import (
    VerifiedBasketImport,
    VerifiedBasketComponent,
    WEIGHT_SUM_TOLERANCE,
    validate_basket_token_mapping,
    is_official_nse_source_url,
)


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_UNDERLYING_RE = re.compile(r"^[A-Z0-9_-]{2,24}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9&._-]{1,32}$")


def _require_https_source(name: str, url: str, label: str) -> tuple[str, str]:
    clean_name = str(name or "").strip()
    clean_url = str(url or "").strip()
    if not clean_name or not is_official_nse_source_url(clean_url):
        raise ValueError(f"{label} name and official NSE HTTPS URL are required")
    return clean_name, clean_url


def _validate_basket_payload(basket: VerifiedBasketImport) -> tuple[tuple[VerifiedBasketComponent, ...], float]:
    """Revalidate the dataclass at the persistence boundary; it is constructible."""
    underlying = str(basket.underlying or "").strip().upper()
    if not _UNDERLYING_RE.fullmatch(underlying):
        raise ValueError("validated basket underlying is invalid")
    if not isinstance(basket.effective_date, date) or isinstance(basket.effective_date, datetime):
        raise ValueError("validated basket effective date must be a calendar date")
    if str(basket.file_format or "").upper() not in {"CSV", "ZIP"}:
        raise ValueError("validated basket file format is invalid")
    if not isinstance(basket.components, (tuple, list)) or not basket.components:
        raise ValueError("validated basket must contain components")

    components: list[VerifiedBasketComponent] = []
    seen_symbols: set[str] = set()
    weights: list[float] = []
    for item in basket.components:
        if not isinstance(item, VerifiedBasketComponent):
            raise ValueError("validated basket contains an invalid component")
        symbol = str(item.symbol or "").strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol) or symbol != item.symbol or symbol in seen_symbols:
            raise ValueError("validated basket contains invalid or duplicate symbols")
        if isinstance(item.weight, bool):
            raise ValueError(f"weight for {symbol} must be finite and positive")
        try:
            weight = float(item.weight)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"weight for {symbol} must be finite and positive") from exc
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"weight for {symbol} must be finite and positive")
        seen_symbols.add(symbol)
        weights.append(weight)
        components.append(VerifiedBasketComponent(symbol=symbol, weight=weight))

    total = math.fsum(weights)
    if not math.isfinite(total) or abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"validated basket weight sum must be 100%; received {total:.6f}%")
    if isinstance(basket.weight_sum, bool):
        raise ValueError("validated basket weight sum is invalid")
    try:
        declared_total = float(basket.weight_sum)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("validated basket weight sum is invalid") from exc
    if not math.isfinite(declared_total) or abs(declared_total - total) > 1e-9:
        raise ValueError("validated basket declared weight sum does not match its components")
    return tuple(components), total


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
    """Append a revalidated basket and complete provenance as VALID only.

    The source file hash is unique per underlying, making retries safe. This
    function does not activate the record or toggle weighted-pressure runtime.
    """
    if not isinstance(basket, VerifiedBasketImport):
        raise ValueError("a validated basket import is required")
    components, weight_sum = _validate_basket_payload(basket)
    if not _SHA256_RE.fullmatch(str(basket.sha256 or "")):
        raise ValueError("validated basket source SHA-256 is invalid")
    if not _SHA256_RE.fullmatch(str(roster_sha256 or "").lower()):
        raise ValueError("authoritative roster SHA-256 is required")
    if created_by_user_id is not None and (
        isinstance(created_by_user_id, bool)
        or not isinstance(created_by_user_id, int)
        or created_by_user_id <= 0
    ):
        raise ValueError("created_by_user_id must be a positive integer when supplied")
    roster_name, roster_url = _require_https_source(
        roster_source_name, roster_source_url, "roster source"
    )
    token_mapping = validate_basket_token_mapping(basket, symbol_to_token)
    source_name, source_url = _require_https_source(
        basket.source_name, basket.source_url, "basket source"
    )
    effective_at = datetime.combine(basket.effective_date, time.min, tzinfo=timezone.utc)
    record = IVConstituentBasketVersion(
        underlying=str(basket.underlying).strip().upper(),
        effective_date=effective_at,
        source_name=source_name,
        source_url=source_url,
        source_sha256=basket.sha256,
        file_format=str(basket.file_format).upper(),
        weight_sum=weight_sum,
        components_json=json.dumps(
            [{"symbol": item.symbol, "weight": item.weight} for item in components],
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
