""""Import the latest official NSE Indices NIFTY and BANKNIFTY monthly baskets.

Run from the activated backend virtualenv on the VPS:
    cd /opt/Algo-Trading/backend && python -m scripts.import_iv_official_baskets

This is an explicit operator CLI. It never enables the IV runner or trading.
Both indexes are fully validated and token-resolved before one DB transaction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from app.core.database import SessionLocal
from app.market_data.instruments import InstrumentMaster
from app.scanner.iv_skew_flip.official_weight_pdf import parse_official_weight_pdf_archive
from app.scanner.iv_skew_flip.weight_store import persist_validated_basket_version


NIFTY_ROSTER_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
BANK_ROSTER_URL = "https://www.niftyindices.com/IndexConstituent/ind_niftybanklist.csv"
ARCHIVE_BASE = "https://www.niftyindices.com/Indices_-_Market_Capitalisation_and_Weightage"
# Angel One publishes the same instrument master from this alternate official
# host. Keep the fallback IV-importer-local: do not couple it to Box Spread's
# isolated cache or relax any of the IV token-resolution checks.
ANGEL_ONE_ALTERNATE_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
INDEXES = {
    "NIFTY": {
        "roster_url": NIFTY_ROSTER_URL,
        "roster_name": "NSE India official NIFTY 50 constituent roster",
        "source_name": "NSE Indices monthly Market Capitalisation and Weightage archive",
    },
    "BANKNIFTY": {
        "roster_url": BANK_ROSTER_URL,
        "roster_name": "NSE Indices official NIFTY Bank constituent roster",
        "source_name": "NSE Indices monthly Market Capitalisation and Weightage archive",
    },
}


def _previous_month_label(today: date) -> str:
    first = today.replace(day=1)
    previous = first.fromordinal(first.toordinal() - 1)
    return previous.strftime("%b%Y")


def _fetch_roster(session: requests.Session, url: str) -> tuple[tuple[str, ...], str]:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    raw = response.content
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or "Symbol" not in (rows[0].keys() if rows else ()):
        raise ValueError(f"official roster has no Symbol column: {url}")
    symbols = tuple(str(row.get("Symbol") or "").strip().upper() for row in rows)
    if any(not symbol for symbol in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError(f"official roster contains blank or duplicate symbols: {url}")
    return symbols, hashlib.sha256(raw).hexdigest()


def _load_alternate_official_master(master: InstrumentMaster) -> list[dict]:
    """Load Angel One's alternate official host into this CLI's master view."""
    response = requests.get(ANGEL_ONE_ALTERNATE_MASTER_URL, timeout=(15, 90))
    response.raise_for_status()
    rows = response.json()
    if (
        not isinstance(rows, list)
        or not rows
        or not all(isinstance(row, dict) for row in rows)
        or not any(
            str(row.get("symbol", "")).strip()
            and str(row.get("exch_seg", "")).strip()
            and str(row.get("token", "")).strip()
            for row in rows
        )
    ):
        raise ValueError("Angel One alternate instrument master is invalid or empty")

    master.instruments = rows
    master._loaded = True
    master._symbol_exchange_index = None
    return rows


def _load_current_instrument_master(master: InstrumentMaster) -> list[dict]:
    """Prefer the primary Angel One URL, then its official alternate host."""
    try:
        return master.download()
    except Exception as primary_error:
        print(
            "Primary Angel One instrument-master URL failed; "
            "trying Angel One's alternate official host: "
            f"{type(primary_error).__name__}: {primary_error}"
        )
        try:
            rows = _load_alternate_official_master(master)
        except Exception as alternate_error:
            raise RuntimeError(
                "Both official Angel One instrument-master hosts failed; "
                "no basket was imported. "
                f"Primary: {primary_error}; alternate: {alternate_error}"
            ) from alternate_error
        print(f"Alternate official Angel One master loaded: rows={len(rows)}")
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive-month",
        default=_previous_month_label(datetime.now(ZoneInfo("Asia/Kolkata")).date()),
        help="NSE Indices monthly archive label, e.g. Oct2026 (default: previous calendar month)",
    )
    parser.add_argument(
        "--as-of",
        default=datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        help="Validation date in YYYY-MM-DD (default: current India date)",
    )
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)
    if not __import__("re").fullmatch(r"[A-Z][a-z]{2}[0-9]{4}", args.archive_month):
        parser.error("--archive-month must look like Oct2026")

    archive_url = f"{ARCHIVE_BASE}/indices_data{args.archive_month}.zip"
    http = requests.Session()
    http.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Referer": "https://www.niftyindices.com/",
    })

    archive_response = http.get(archive_url, timeout=90)
    archive_response.raise_for_status()
    archive_bytes = archive_response.content
    print(f"Official monthly ZIP: {archive_url}")
    print(f"HTTP: {archive_response.status_code}; bytes: {len(archive_bytes)}")
    print(f"ZIP SHA256: {hashlib.sha256(archive_bytes).hexdigest()}")

    prepared = {}
    for underlying, config in INDEXES.items():
        symbols, roster_hash = _fetch_roster(http, config["roster_url"])
        basket = parse_official_weight_pdf_archive(
            archive_bytes,
            filename=f"indices_data{args.archive_month}.zip",
            underlying=underlying,
            source_name=config["source_name"],
            source_url=archive_url,
            expected_symbols=symbols,
            as_of=as_of,
        )
        prepared[underlying] = (basket, symbols, roster_hash, config)
        print(
            f"{underlying}: constituents={len(basket.components)}, "
            f"effective_date={basket.effective_date.isoformat()}, "
            f"weight_sum={basket.weight_sum:.2f}%, "
            f"roster_sha256={roster_hash}"
        )

    # Resolve every symbol against the current Angel One NSE cash master before
    # opening a transaction. Missing/ambiguous/stale instruments abort both.
    master = InstrumentMaster()
    _load_current_instrument_master(master)
    mappings = {}
    for underlying, (basket, _symbols, _hash, _config) in prepared.items():
        mappings[underlying] = {
            component.symbol: master.resolve_cash_token(component.symbol, "NSE")
            for component in basket.components
        }
        print(f"{underlying}: current NSE cash tokens resolved={len(mappings[underlying])}")

    # One transaction prevents a half-import where only one index is stored.
    with SessionLocal.begin() as db:
        for underlying, (basket, _symbols, roster_hash, config) in prepared.items():
            record = persist_validated_basket_version(
                db,
                basket,
                mappings[underlying],
                roster_source_name=config["roster_name"],
                roster_source_url=config["roster_url"],
                roster_sha256=roster_hash,
                commit=False,
            )
            print(
                f"{underlying}: imported record_id={record.id}, "
                f"effective_date={basket.effective_date.isoformat()}"
            )

    print("Both official baskets imported atomically.")
    print("IV weighted-pressure activation remains OFF; broker orders remain OFF.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
