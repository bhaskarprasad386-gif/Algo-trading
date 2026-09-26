"""Probe and persist Angel One Cash + currently published Future history.

This is deliberately separate from the full historical-contract workflow: it uses only
the provider's current instrument master, never relabels it as a historical snapshot,
and walks backward in bounded 30-day API windows. Empty/unavailable windows are
reported and never fabricated.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.backtesting.angelone_contract_master import AngelOneContractMasterSource
from app.backtesting.angelone_historical import AngelOneHistoricalSource
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.market_data.instruments import InstrumentMaster

IST = ZoneInfo("Asia/Kolkata")
DAY_NS = 86_400_000_000_000
INTERVAL_NS = 60_000_000_000


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _market_bounds(day: date) -> tuple[int, int]:
    return _ns(datetime.combine(day, time(9, 15), tzinfo=IST)), _ns(
        datetime.combine(day, time(15, 30), tzinfo=IST)
    )


def _published_futures(rows, today: date):
    records = AngelOneContractMasterSource.normalize_futures(rows)
    # Current master is authoritative only for what it currently publishes.
    # Do not invent expired contracts or historical snapshot dates.
    return tuple(sorted(
        (r for r in records if r.expiry >= today),
        key=lambda r: (r.underlying, r.expiry, r.token),
    ))


def _fetch_window(ingestion, source, *, instrument: str, start_day: date, end_day: date) -> tuple[int, int]:
    start_ns, _ = _market_bounds(start_day)
    _, end_ns = _market_bounds(end_day)
    request = HistoricalFetchRequest(
        source="angelone",
        instrument=instrument,
        timeframe="1m",
        start_ns=start_ns,
        end_ns=end_ns,
    )
    result = ingestion.sync_streaming(source, request, batch_size=1024)
    return result.inserted, result.fetched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--max-underlyings", type=int, default=0)
    parser.add_argument("--db", default="backtest_market_data.sqlite3")
    args = parser.parse_args()
    if args.lookback_days < 1 or args.chunk_days < 1:
        raise SystemExit("lookback-days and chunk-days must be positive")

    today = datetime.now(IST).date()
    start_limit = today - timedelta(days=args.lookback_days)
    master_rows = InstrumentMaster().download()
    published = _published_futures(master_rows, today)
    # Keep CURRENT + NEAR semantics: at most the first two published expiries
    # per stock. This avoids multiplying API calls across far contracts.
    selected: list[object] = []
    by_underlying: dict[str, list[object]] = {}
    for record in published:
        by_underlying.setdefault(record.underlying, []).append(record)
    ordered_underlyings = sorted(by_underlying)
    if args.max_underlyings:
        ordered_underlyings = ordered_underlyings[: args.max_underlyings]
    for underlying in ordered_underlyings:
        selected.extend(by_underlying[underlying][:2])
    futures = tuple(selected)
    if not futures:
        raise SystemExit("No currently published stock-future contracts found")

    auth = AngelOneAuth()
    auth.get_client()
    source = AngelOneHistoricalSource(auth=auth, chunk_days=min(args.chunk_days, 30))
    catalog = HistoricalCatalog(args.db)
    ingestion = HistoricalIngestionService(catalog)
    instruments = InstrumentMaster()
    cash_done: set[str] = set()

    print(f"PROBE_DATE={today.isoformat()}")
    print(f"PUBLISHED_STOCK_FUTURES={len(futures)}")
    print(f"LOOKBACK_START={start_limit.isoformat()}")

    try:
        for contract in futures:
            future_instrument = f"NFO:{contract.token}:{contract.symbol}"
            current_end = min(today, contract.expiry)
            if current_end < start_limit:
                continue
            print(
                f"FUTURE_START underlying={contract.underlying} "
                f"expiry={contract.expiry.isoformat()} token={contract.token}"
            )
            day = current_end
            first_success: date | None = None
            last_success: date | None = None
            while day >= start_limit:
                window_start = max(start_limit, day - timedelta(days=args.chunk_days - 1))
                try:
                    inserted, fetched = _fetch_window(
                        ingestion, source, instrument=future_instrument,
                        start_day=window_start, end_day=day,
                    )
                    print(
                        f"FUTURE_WINDOW {contract.underlying} {window_start}..{day} "
                        f"fetched={fetched} inserted={inserted}"
                    )
                    if fetched:
                        first_success = window_start if first_success is None else first_success
                        last_success = day
                    else:
                        print(
                            f"FUTURE_WINDOW_UNAVAILABLE {contract.underlying} "
                            f"{window_start}..{day}"
                        )
                        # Moving farther back cannot recover a contract before
                        # its first published/traded history; stop this token.
                        break
                except Exception as exc:
                    print(
                        f"FUTURE_WINDOW_ERROR {contract.underlying} "
                        f"{window_start}..{day} error={exc}"
                    )
                day = window_start - timedelta(days=1)

            try:
                cash_instrument = instruments.resolve_cash_instrument(contract.underlying, "NSE")
            except (LookupError, ValueError) as exc:
                # The Angel master can contain non-tradable/test future rows with
                # no matching NSE cash leg. They are not Cash-Future candidates;
                # skip them rather than aborting the entire probe.
                print(
                    f"FUTURE_SKIP_NO_CASH underlying={contract.underlying} "
                    f"token={contract.token} reason={exc}"
                )
                continue
            cash_token = str(cash_instrument["token"])
            cash_key = f"NSE:{cash_token}:{contract.underlying}"
            if cash_key not in cash_done:
                cash_done.add(cash_key)
                day = today
                print(f"CASH_START underlying={contract.underlying} token={cash_token}")
                while day >= start_limit:
                    window_start = max(start_limit, day - timedelta(days=args.chunk_days - 1))
                    try:
                        inserted, fetched = _fetch_window(
                            ingestion, source, instrument=cash_key,
                            start_day=window_start, end_day=day,
                        )
                        print(
                            f"CASH_WINDOW {contract.underlying} {window_start}..{day} "
                            f"fetched={fetched} inserted={inserted}"
                        )
                    except Exception as exc:
                        print(
                            f"CASH_WINDOW_ERROR {contract.underlying} "
                            f"{window_start}..{day} error={exc}"
                        )
                    day = window_start - timedelta(days=1)

            print(
                f"FUTURE_DONE underlying={contract.underlying} "
                f"token={contract.token} expiry={contract.expiry.isoformat()}"
            )
    finally:
        catalog.close()

    print("AVAILABLE_CASH_FUTURE_HISTORY_PROBE=COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
