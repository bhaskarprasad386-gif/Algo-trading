"""Import the latest official NSE Indices NIFTY and BANKNIFTY monthly baskets.

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
import re
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


def _normalise_csv_header(value: str | None) -> str:
    return re.sub(r"[^a-z]", "", (value or "").replace("\ufeff", "").strip().lower())


def _fetch_roster(session: requests.Session, url: str) -> tuple[tuple[str, ...], str]:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    raw = response.content
    text = raw.decode("utf-8-sig")
    csv_rows = list(csv.reader(io.StringIO(text)))
    # Some official constituent downloads prepend metadata or vary header case/
    # whitespace. Locate the actual Symbol header instead of assuming row one.
    header_index = next(
        (
            index for index, row in enumerate(csv_rows)
            if any(_normalise_csv_header(cell) == "symbol" for cell in row)
        ),
        None,
    )
    if header_index is None:
        preview = " | ".join(",".join(row) for row in csv_rows[:3])
        raise ValueError(
            f"official roster has no Symbol column: {url}; "
            f"received {len(csv_rows)} CSV rows; first rows: {preview[:300]!r}"
        )

    headers = csv_rows[header_index]
    symbol_index = next(
        index for index, cell in enumerate(headers)
        if _normalise_csv_header(cell) == "symbol"
    )
    symbols_list = []
    for row in csv_rows[header_index + 1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        symbol = row[symbol_index].strip().upper() if symbol_index < len(row) else ""
        symbols_list.append(symbol)

    symbols = tuple(symbols_list)
    if not symbols or any(not symbol for symbol in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError(f"official roster contains no symbols, blanks, or duplicate symbols: {url}")
    return symbols, hashlib.sha256(raw).hexdigest()


def _load_current_instrument_master(master: InstrumentMaster) -> list[dict]:
    """Retry Angel One's documented master URL once with a longer timeout."""
    try:
        rows = master.download()
    except Exception as primary_error:
        print(
            "Angel One instrument-master request failed; retrying the same "
            "documented official URL with an extended timeout: "
            f"{type(primary_error).__name__}: {primary_error}"
        )
        try:
            rows = master.download(force=True, timeout=(15, 120))
        except Exception as retry_error:
            raise RuntimeError(
                "Angel One official instrument master failed on both attempts; "
                "no basket was imported. "
                f"First attempt: {primary_error}; retry: {retry_error}"
            ) from retry_error
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Angel One official instrument master is empty; no basket was imported")
    print(f"Official Angel One instrument master loaded: rows={len(rows)}")
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
    if not re.fullmatch(r"[A-Z][a-z]{2}[0-9]{4}", args.archive_month):
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
