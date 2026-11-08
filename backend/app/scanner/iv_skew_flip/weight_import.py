"""Strict, fail-closed import validation for official IV constituent baskets.

This module deliberately does not fetch, invent, normalize, or activate index
weights. Callers must provide an authoritative expected-symbol roster and
source provenance. Persistence and runtime activation are separate concerns.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping
from urllib.parse import urlparse


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ZIP_RATIO = 100
WEIGHT_SUM_TOLERANCE = 0.25
_REQUIRED_COLUMNS = ("symbol", "weight")
_SYMBOL_RE = re.compile(r"^[A-Z0-9&._-]{1,32}$")


@dataclass(frozen=True)
class VerifiedBasketComponent:
    symbol: str
    weight: float


@dataclass(frozen=True)
class VerifiedBasketImport:
    underlying: str
    effective_date: date
    source_name: str
    source_url: str
    sha256: str
    file_format: str
    components: tuple[VerifiedBasketComponent, ...]
    weight_sum: float


def _normalized_symbols(values: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        symbol = str(value or "").strip().upper()
        if not symbol or not _SYMBOL_RE.fullmatch(symbol) or symbol in result:
            raise ValueError("expected constituent roster must contain unique valid symbols")
        result.add(symbol)
    if not result:
        raise ValueError("authoritative expected constituent roster is required")
    return result


def _extract_csv(raw: bytes, filename: str) -> tuple[bytes, str]:
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("upload is empty or exceeds the 10 MiB limit")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "csv":
        return raw, "CSV"
    if suffix != "zip":
        raise ValueError("only CSV or ZIP uploads are accepted")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if len(files) != 1 or not files[0].filename.lower().endswith(".csv"):
                raise ValueError("ZIP must contain exactly one CSV file")
            item = files[0]
            if item.file_size <= 0 or item.file_size > MAX_UPLOAD_BYTES:
                raise ValueError("ZIP CSV is empty or exceeds the 10 MiB limit")
            if item.compress_size == 0 or item.file_size / item.compress_size > MAX_ZIP_RATIO:
                raise ValueError("ZIP compression ratio exceeds the safety limit")
            if item.flag_bits & 0x1:
                raise ValueError("encrypted ZIP entries are not supported")
            return archive.read(item), "ZIP"
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid ZIP archive") from exc


def parse_official_weight_basket(
    raw: bytes,
    *,
    filename: str,
    underlying: str,
    effective_date: str | date,
    source_name: str,
    source_url: str,
    expected_symbols: Iterable[str],
    as_of: date,
    max_age_days: int = 45,
) -> VerifiedBasketImport:
    """Parse one complete official weight file; never accept partial baskets.

    CSV columns are exactly 'symbol,weight'. Weights are percentage points and
    must total 100 within a small rounding tolerance. The expected roster must
    come from a separately verified official source for the same index/date.
    """
    index = str(underlying or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{2,24}", index):
        raise ValueError("underlying is invalid")
    if not isinstance(as_of, date):
        raise ValueError("as_of must be a date")
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 0:
        raise ValueError("max_age_days must be a non-negative integer")
    try:
        effective = effective_date if isinstance(effective_date, date) else date.fromisoformat(str(effective_date))
    except (TypeError, ValueError) as exc:
        raise ValueError("effective_date must be an ISO date") from exc
    if effective > as_of:
        raise ValueError("future-dated constituent weights are not accepted")
    if (as_of - effective).days > max_age_days:
        raise ValueError("constituent weights are stale; import the latest official file")
    source = str(source_name or "").strip()
    parsed_url = urlparse(str(source_url or "").strip())
    if not source or parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("source name and HTTPS source URL are required")

    expected = _normalized_symbols(expected_symbols)
    csv_bytes, file_format = _extract_csv(raw, filename)
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = tuple((header or "").strip().lower() for header in (reader.fieldnames or ()))
    if len(headers) != 2 or set(headers) != set(_REQUIRED_COLUMNS):
        raise ValueError("CSV must contain exactly the symbol and weight columns")
    reader.fieldnames = list(headers)
    parsed: dict[str, float] = {}
    for line_number, row in enumerate(reader, start=2):
        if None in row or row.get("symbol") is None or row.get("weight") is None:
            raise ValueError(f"malformed CSV row on line {line_number}")
        symbol = str(row.get("symbol") or "").strip().upper()
        raw_weight = str(row.get("weight") or "").strip()
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError(f"invalid symbol on CSV line {line_number}")
        if symbol in parsed:
            raise ValueError(f"duplicate constituent symbol: {symbol}")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid weight for {symbol}") from exc
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"weight for {symbol} must be finite and positive")
        parsed[symbol] = weight
    if not parsed:
        raise ValueError("CSV contains no constituent rows")
    missing = sorted(expected - parsed.keys())
    unexpected = sorted(parsed.keys() - expected)
    if missing or unexpected:
        raise ValueError(f"constituent roster mismatch; missing={missing}; unexpected={unexpected}")
    total = math.fsum(parsed.values())
    if not math.isfinite(total) or abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"weight sum must be 100%; received {total:.6f}%")
    components = tuple(
        VerifiedBasketComponent(symbol=symbol, weight=parsed[symbol])
        for symbol in sorted(parsed)
    )
    return VerifiedBasketImport(
        underlying=index,
        effective_date=effective,
        source_name=source,
        source_url=parsed_url.geturl(),
        sha256=hashlib.sha256(raw).hexdigest(),
        file_format=file_format,
        components=components,
        weight_sum=total,
    )


def validate_basket_token_mapping(
    basket: VerifiedBasketImport,
    symbol_to_token: Mapping[str, object],
) -> dict[str, str]:
    """Require a unique concrete instrument token for every imported symbol."""
    expected_symbols = {component.symbol for component in basket.components}
    supplied_symbols = {str(symbol).strip().upper() for symbol in symbol_to_token}
    unexpected = sorted(supplied_symbols - expected_symbols)
    if unexpected:
        raise ValueError(f"unexpected symbols in NSE cash token mapping: {unexpected}")

    mapped: dict[str, str] = {}
    seen_tokens: set[str] = set()
    for component in basket.components:
        raw_token = symbol_to_token.get(component.symbol)
        if isinstance(raw_token, bool) or raw_token is None:
            raise ValueError(f"missing or invalid NSE cash instrument token for {component.symbol}")
        token = str(raw_token).strip()
        if not re.fullmatch(r"[1-9][0-9]*", token):
            raise ValueError(f"invalid NSE cash instrument token for {component.symbol}")
        if token in seen_tokens:
            raise ValueError(f"duplicate NSE cash instrument token: {token}")
        seen_tokens.add(token)
        mapped[component.symbol] = token
    return mapped
