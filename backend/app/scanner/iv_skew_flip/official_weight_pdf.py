"""Fail-closed parser for official NSE Indices monthly constituent-weight PDFs."""
from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from datetime import date, datetime
from typing import Iterable
from urllib.parse import urlparse

from pypdf import PdfReader

from app.scanner.iv_skew_flip.weight_import import (
    MAX_UPLOAD_BYTES,
    WEIGHT_SUM_TOLERANCE,
    VerifiedBasketComponent,
    VerifiedBasketImport,
    _normalized_symbols,
    is_official_nse_source_url,
)

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_PDF_BY_UNDERLYING = {
    "NIFTY": "NIFTY_50",
    "BANKNIFTY": "NIFTY_Bank",
}


def _extract_weight_from_line(line: str) -> float | None:
    parts = line.split()
    if not parts:
        return None
    candidate = parts[-1].replace(",", "")
    if not _NUMBER_RE.fullmatch(candidate):
        return None
    value = float(candidate)
    return value if math.isfinite(value) and value > 0 else None


def _parse_pdf_text(text: str, expected_symbols: set[str]) -> tuple[date, dict[str, float]]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("official constituent PDF contains no extractable text")
    date_match = _DATE_RE.search(text)
    if not date_match:
        raise ValueError("official constituent PDF effective date is missing")
    effective = datetime.strptime(
        f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}",
        "%B %d %Y",
    ).date()

    lines = text.splitlines()
    found: dict[str, float] = {}
    for index, line in enumerate(lines):
        parts = line.split()
        if not parts:
            continue
        symbol = parts[0].strip().upper()
        if symbol not in expected_symbols:
            continue
        if symbol in found:
            raise ValueError(f"duplicate constituent row in official PDF: {symbol}")

        weight = _extract_weight_from_line(line)
        if weight is None:
            # Some PDF table rows wrap the company name and final weight onto
            # the following line (e.g. ADANIPORTS). Search only until the next
            # recognized constituent symbol; never borrow a later row's value.
            for following in lines[index + 1:]:
                next_parts = following.split()
                if next_parts and next_parts[0].strip().upper() in expected_symbols:
                    break
                weight = _extract_weight_from_line(following)
                if weight is not None:
                    break
        if weight is None:
            raise ValueError(f"weightage is missing for {symbol} in official PDF")
        found[symbol] = weight

    missing = sorted(expected_symbols - found.keys())
    unexpected = sorted(found.keys() - expected_symbols)
    if missing or unexpected:
        raise ValueError(
            f"official PDF constituent roster mismatch; missing={missing}; unexpected={unexpected}"
        )
    total = math.fsum(found.values())
    if not math.isfinite(total) or abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"official PDF weight sum must be 100%; received {total:.6f}%")
    return effective, found


def parse_official_weight_pdf_archive(
    raw: bytes,
    *,
    filename: str,
    underlying: str,
    source_name: str,
    source_url: str,
    expected_symbols: Iterable[str],
    as_of: date,
    max_age_days: int = 45,
) -> VerifiedBasketImport:
    """Parse the exact index PDF from an official monthly ZIP and verify all rows."""
    index = str(underlying or "").strip().upper()
    if index not in _PDF_BY_UNDERLYING:
        raise ValueError("underlying must be NIFTY or BANKNIFTY")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError("as_of must be a calendar date")
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("official ZIP is empty or exceeds the 10 MiB limit")
    if not isinstance(filename, str) or not filename.lower().endswith(".zip"):
        raise ValueError("official monthly weight archive must be a ZIP file")
    if not isinstance(max_age_days, int) or isinstance(max_age_days, bool) or max_age_days < 0:
        raise ValueError("max_age_days must be a non-negative integer")
    expected = _normalized_symbols(expected_symbols)
    clean_name = str(source_name or "").strip()
    clean_url = str(source_url or "").strip()
    if not clean_name or not is_official_nse_source_url(clean_url):
        raise ValueError("official NSE Indices source name and HTTPS URL are required")

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if len(files) > 32:
                raise ValueError("official monthly ZIP contains too many entries")
            target_name = f"{_PDF_BY_UNDERLYING[index]}_"
            matches = [
                item for item in files
                if item.filename.rsplit("/", 1)[-1].startswith(target_name)
                and item.filename.lower().endswith(".pdf")
            ]
            if len(matches) != 1:
                raise ValueError(f"official monthly ZIP must contain exactly one {index} constituent PDF")
            item = matches[0]
            if item.file_size <= 0 or item.file_size > 5 * 1024 * 1024:
                raise ValueError("official constituent PDF is empty or exceeds the 5 MiB limit")
            if item.compress_size <= 0 or item.file_size / item.compress_size > 100:
                raise ValueError("official constituent PDF compression ratio exceeds the safety limit")
            if item.flag_bits & 0x1:
                raise ValueError("encrypted official PDF entries are not supported")
            pdf_bytes = archive.read(item)
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid official monthly weight ZIP archive") from exc

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
        if len(reader.pages) < 1 or len(reader.pages) > 10:
            raise ValueError("official constituent PDF has an invalid page count")
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ValueError("official constituent PDF could not be safely parsed") from exc

    effective, weights = _parse_pdf_text(extracted, expected)
    if effective > as_of:
        raise ValueError("future-dated constituent weights are not accepted")
    if (as_of - effective).days > max_age_days:
        raise ValueError("constituent weights are stale; fetch the latest official monthly archive")

    components = tuple(
        VerifiedBasketComponent(symbol=symbol, weight=weights[symbol])
        for symbol in sorted(weights)
    )
    total = math.fsum(item.weight for item in components)
    return VerifiedBasketImport(
        underlying=index,
        effective_date=effective,
        source_name=clean_name,
        source_url=urlparse(clean_url).geturl(),
        sha256=hashlib.sha256(raw).hexdigest(),
        file_format="PDFZIP",
        components=components,
        weight_sum=total,
    )
