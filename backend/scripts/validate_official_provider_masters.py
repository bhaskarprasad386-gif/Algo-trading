#!/usr/bin/env python3
"""Fetch official public broker NFO and BFO masters and validate parser/coverage offline.

Read-only: no broker login, credentials, WebSocket, subscriptions or order calls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# Running this file directly sets sys.path[0] to backend/scripts; add the
# backend package root so the repository application modules are importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from app.market_data.provider_master_audit import audit_master_coverage
from app.market_data.provider_master_parsers import AngelOneMasterParser, MasterParseReport, QuarantinedMasterRow, ShoonyaMasterParser

ANGEL_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SHOONYA_URLS = {
    "NFO": "https://api.shoonya.com/NFO_symbols.txt.zip",
    "BFO": "https://api.shoonya.com/BFO_symbols.txt.zip",
}
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
TIMEOUT = (15, 120)


def download(url: str) -> bytes:
    response = requests.get(
        url,
        headers={"User-Agent": "Algo-Trading-Official-Master-Validation/1.0"},
        timeout=TIMEOUT,
        stream=True,
    )
    response.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}")
            chunks.append(chunk)
    finally:
        response.close()
    payload = b"".join(chunks)
    if not payload:
        raise ValueError(f"empty download: {url}")
    return payload


def normalized(row: dict[str, Any], *names: str) -> str:
    values = {str(k).strip().lower().replace("_", "").replace(" ", ""): v for k, v in row.items()}
    for name in names:
        value = values.get(name.strip().lower().replace("_", "").replace(" ", ""))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def load_angel_segment(raw: bytes, exchange: str) -> tuple[list[dict[str, Any]], str]:
    rows = json.loads(raw.decode("utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Angel One official master is not a non-empty JSON array")
    selected = [
        row for row in rows
        if isinstance(row, dict)
        and normalized(row, "exch_seg", "exchange", "exch").upper() == exchange
    ]
    if not selected:
        raise ValueError(f"Angel One official master contains no {exchange} rows")
    return selected, hashlib.sha256(raw).hexdigest()


def load_shoonya_segment(raw_zip: bytes, exchange: str) -> tuple[bytes, str, str]:
    if not zipfile.is_zipfile(io.BytesIO(raw_zip)):
        raise ValueError(f"Shoonya official {exchange} download is not a ZIP archive")
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        candidates = [n for n in archive.namelist() if n.lower().endswith((".txt", ".csv")) and not n.endswith("/")]
        if len(candidates) != 1:
            raise ValueError(f"expected one text master in Shoonya {exchange} ZIP, found {len(candidates)}")
        name = candidates[0]
        info = archive.getinfo(name)
        if info.file_size > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Shoonya {exchange} extracted master exceeds size limit")
        payload = archive.read(name)
    text = payload.decode("utf-8-sig")
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    counts = {delimiter: first_line.count(delimiter) for delimiter in (",", "|", "\t")}
    delimiter = max(counts, key=counts.get)
    if counts[delimiter] == 0:
        raise ValueError(f"cannot detect Shoonya {exchange} delimiter from header")
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError(f"Shoonya {exchange} official master has no header")
    required = {"exchange", "token", "lotsize", "tradingsymbol", "instrument"}
    actual = {str(x).strip().lower().replace("_", "").replace(" ", "") for x in reader.fieldnames}
    missing = required - actual
    if missing:
        raise ValueError(f"Shoonya {exchange} header missing required columns: {sorted(missing)}")
    rows = list(reader)
    selected = [row for row in rows if normalized(row, "Exchange", "exch").upper() == exchange]
    if not selected:
        raise ValueError(f"Shoonya official master contains no {exchange} rows")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames, delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    writer.writerows(selected)
    return output.getvalue().encode("utf-8"), hashlib.sha256(raw_zip).hexdigest(), name


def combine_segment_reports(broker: str, reports: list[MasterParseReport]) -> MasterParseReport:
    mappings = tuple(item for report in reports for item in report.mappings)
    quarantined = []
    offset = 0
    for report in reports:
        quarantined.extend(
            QuarantinedMasterRow(offset + item.row_number, item.reason)
            for item in report.quarantined
        )
        offset += report.total_rows
    digest = hashlib.sha256("|".join(report.source_sha256 for report in reports).encode()).hexdigest()
    return MasterParseReport(
        broker=broker,
        source_sha256=digest,
        mappings=mappings,
        quarantined=tuple(quarantined),
        total_rows=sum(report.total_rows for report in reports),
    )


def parse_delimited_shoonya(payload: bytes) -> MasterParseReport:
    return ShoonyaMasterParser.parse(
        payload, delimiter=detect_delimiter(payload),
        strike_scale=Decimal("1"), tick_scale=Decimal("1"),
    )

def contract_key_summary(key: Any) -> dict[str, Any]:
    return {
        "exchange": key.exchange,
        "segment": key.segment,
        "underlying": key.underlying,
        "instrument_type": key.instrument_type,
        "expiry": key.expiry.isoformat() if key.expiry else None,
        "strike": str(key.strike) if key.strike is not None else None,
        "option_type": key.option_type,
    }


def mapping_breakdown(report: Any) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    expiries = []
    expired = 0
    today = datetime.now(timezone.utc).date()
    for item in report.mappings:
        by_type[item.key.instrument_type] = by_type.get(item.key.instrument_type, 0) + 1
        if item.key.expiry is not None:
            expiries.append(item.key.expiry)
            if item.key.expiry < today:
                expired += 1
    return {
        "by_instrument_type": dict(sorted(by_type.items())),
        "unique_expiries": len(set(expiries)),
        "earliest_expiry": min(expiries).isoformat() if expiries else None,
        "latest_expiry": max(expiries).isoformat() if expiries else None,
        "expired_contracts_at_validation_date": expired,
        "non_expired_contracts_at_validation_date": len(report.mappings) - expired,
    }


def underlying_inventory(angel_report: Any, shoonya_report: Any) -> dict[str, Any]:
    angel = {item.key.underlying for item in angel_report.mappings}
    shoonya = {item.key.underlying for item in shoonya_report.mappings}
    return {
        "angel_unique_underlyings": len(angel),
        "shoonya_unique_underlyings": len(shoonya),
        "common_underlyings": len(angel & shoonya),
        "angel_only_underlying_sample": sorted(angel - shoonya)[:40],
        "shoonya_only_underlying_sample": sorted(shoonya - angel)[:40],
        "360ONE": {
            "angel_contracts": sum(1 for item in angel_report.mappings if item.key.underlying == "360ONE"),
            "shoonya_contracts": sum(1 for item in shoonya_report.mappings if item.key.underlying == "360ONE"),
        },
    }


def index_underlying_coverage(angel_report: Any, shoonya_report: Any) -> dict[str, Any]:
    targets = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX")
    result: dict[str, Any] = {}
    for underlying in targets:
        angel = {item.key for item in angel_report.mappings if item.key.underlying == underlying}
        shoonya = {item.key for item in shoonya_report.mappings if item.key.underlying == underlying}
        common = angel & shoonya
        expiries = sorted({key.expiry.isoformat() for key in common if key.expiry is not None})
        angel_only = sorted(angel - shoonya, key=lambda key: (key.expiry.isoformat() if key.expiry else "", str(key.strike), key.option_type or ""))
        shoonya_only = sorted(shoonya - angel, key=lambda key: (key.expiry.isoformat() if key.expiry else "", str(key.strike), key.option_type or ""))
        def expiry_counts(keys: set[Any]) -> dict[str, int]:
            counts: dict[str, int] = {}
            for key in keys:
                if key.expiry is not None:
                    expiry = key.expiry.isoformat()
                    counts[expiry] = counts.get(expiry, 0) + 1
            return dict(sorted(counts.items()))
        result[underlying] = {
            "angel_contracts": len(angel),
            "shoonya_contracts": len(shoonya),
            "common_contracts": len(common),
            "angel_only_contracts": len(angel_only),
            "shoonya_only_contracts": len(shoonya_only),
            "common_option_contracts": sum(1 for key in common if key.instrument_type == "OPTION"),
            "common_future_contracts": sum(1 for key in common if key.instrument_type == "FUTURE"),
            "angel_expiries": sorted({key.expiry.isoformat() for key in angel if key.expiry is not None}),
            "shoonya_expiries": sorted({key.expiry.isoformat() for key in shoonya if key.expiry is not None}),
            "common_expiries": expiries,
            "angel_only_by_expiry": expiry_counts(set(angel_only)),
            "shoonya_only_by_expiry": expiry_counts(set(shoonya_only)),
            "angel_only_sample": [contract_key_summary(key) for key in angel_only[:10]],
            "shoonya_only_sample": [contract_key_summary(key) for key in shoonya_only[:10]],
        }
    return result


def bse_index_symbol_diagnostics(report: Any) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"SENSEX": [], "BANKEX": []}
    for item in report.mappings:
        key = item.key
        for target in result:
            if target in key.underlying or target in item.trading_symbol.upper():
                if len(result[target]) < 20:
                    result[target].append({
                        "underlying": key.underlying,
                        "trading_symbol": item.trading_symbol,
                        "instrument_type": key.instrument_type,
                        "expiry": key.expiry.isoformat() if key.expiry else None,
                        "strike": str(key.strike) if key.strike is not None else None,
                        "token": item.provider.token,
                    })
    return result


def report_summary(report: Any) -> dict[str, Any]:
    return {
        "broker": report.broker,
        "rows": report.total_rows,
        "accepted": report.accepted_rows,
        "rejected": report.rejected_rows,
        "rejection_ratio": round(report.rejection_ratio, 8),
        "parser_payload_sha256": report.source_sha256,
        "quarantine_sample": [
            {"row": item.row_number, "reason": item.reason}
            for item in report.quarantined[:20]
        ],
        "breakdown": mapping_breakdown(report),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="official-provider-master-validation.json")
    parser.add_argument("--max-rejection-ratio", type=Decimal, default=Decimal("0.02"))
    args = parser.parse_args()

    fetched_at = datetime.now(timezone.utc).isoformat()
    angel_raw = download(ANGEL_URL)
    angel_segment_reports = []
    shoonya_segment_reports = []
    source_report = {
        "angel_one": {
            "url": ANGEL_URL,
            "sha256": hashlib.sha256(angel_raw).hexdigest(),
            "download_bytes": len(angel_raw),
        },
        "shoonya": {},
    }

    for exchange, shoonya_url in SHOONYA_URLS.items():
        angel_rows, _ = load_angel_segment(angel_raw, exchange)
        angel_segment_reports.append(AngelOneMasterParser.parse(angel_rows))

        shoonya_zip = download(shoonya_url)
        shoonya_payload, shoonya_source_sha, shoonya_member = load_shoonya_segment(shoonya_zip, exchange)
        shoonya_segment_reports.append(parse_delimited_shoonya(shoonya_payload))
        source_report["shoonya"][exchange] = {
            "url": shoonya_url,
            "zip_sha256": shoonya_source_sha,
            "download_bytes": len(shoonya_zip),
            "member": shoonya_member,
        }

    angel_report = combine_segment_reports("ANGELONE", angel_segment_reports)
    shoonya_report = combine_segment_reports("SHOONYA", shoonya_segment_reports)
    audit = audit_master_coverage((angel_report, shoonya_report))
    index_coverage = index_underlying_coverage(angel_report, shoonya_report)
    index_coverage_gaps = [
        {
            "underlying": underlying,
            "angel_only_contracts": values["angel_only_contracts"],
            "angel_only_by_expiry": values["angel_only_by_expiry"],
            "shoonya_only_contracts": values["shoonya_only_contracts"],
        }
        for underlying, values in index_coverage.items()
        if values["angel_only_contracts"] or values["shoonya_only_contracts"]
    ]

    output = {
        "schema_version": 1,
        "fetched_at_utc": fetched_at,
        "mode": "read_only_official_source_validation",
        "sources": source_report,
        "parse": {
            "angel_one": report_summary(angel_report),
            "shoonya": report_summary(shoonya_report),
        },
        "coverage": {
            "angel_contracts": audit.angel_contracts,
            "shoonya_contracts": audit.shoonya_contracts,
            "common_contracts": audit.common_contracts,
            "angel_only_count": len(audit.angel_only),
            "shoonya_only_count": len(audit.shoonya_only),
            "angel_only_ratio": round(audit.angel_only_ratio, 8),
            "shoonya_only_ratio": round(audit.shoonya_only_ratio, 8),
            "metadata_mismatch_count": len(audit.metadata_mismatches),
            "metadata_mismatch_sample": [str(key) for key in audit.metadata_mismatches[:25]],
            "duplicate_canonical_keys": len(audit.duplicate_canonical_keys),
            "index_underlying_coverage": index_coverage,
            "index_coverage_status": "PASS" if not index_coverage_gaps else "PASS_WITH_COVERAGE_GAPS",
            "index_coverage_gaps": index_coverage_gaps,
            "underlying_inventory": underlying_inventory(angel_report, shoonya_report),
            "bse_index_symbol_diagnostics": {
                "angel_one": bse_index_symbol_diagnostics(angel_report),
                "shoonya": bse_index_symbol_diagnostics(shoonya_report),
            },
            "angel_only_sample": [contract_key_summary(key) for key in audit.angel_only[:15]],
            "shoonya_only_sample": [contract_key_summary(key) for key in audit.shoonya_only[:15]],
        },
    }
    Path(args.report).write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))

    failures = []
    for item in (angel_report, shoonya_report):
        if item.total_rows == 0 or item.accepted_rows == 0:
            failures.append(f"{item.broker}: no accepted contracts")
        if Decimal(str(item.rejection_ratio)) > args.max_rejection_ratio:
            failures.append(f"{item.broker}: rejection ratio {item.rejection_ratio:.4%} exceeds {args.max_rejection_ratio:.4%}")
    if audit.common_contracts == 0:
        failures.append("no canonical contracts are common to both brokers")
    if audit.metadata_mismatches:
        failures.append(f"{len(audit.metadata_mismatches)} common contracts have lot/tick metadata mismatches")
    if audit.duplicate_canonical_keys:
        failures.append(f"{len(audit.duplicate_canonical_keys)} duplicate canonical keys found")
    if failures:
        print("OFFICIAL MASTER VALIDATION: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("OFFICIAL MASTER VALIDATION: PASS_WITH_COVERAGE_GAPS" if index_coverage_gaps else "OFFICIAL MASTER VALIDATION: PASS")
    return 0


def detect_delimiter(payload: bytes) -> str:
    text = payload.decode("utf-8-sig")
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    counts = {delimiter: first_line.count(delimiter) for delimiter in (",", "|", "\t")}
    delimiter = max(counts, key=counts.get)
    if counts[delimiter] == 0:
        raise ValueError("cannot detect Shoonya delimiter")
    return delimiter


if __name__ == "__main__":
    raise SystemExit(main())
