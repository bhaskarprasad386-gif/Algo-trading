"""Probe genuine historical expired stock-future contracts from dated snapshots.

Unlike the availability probe, this script deliberately uses the persisted historical
contract-master snapshots. It never treats the current Angel One instrument master as
an expired-contract snapshot. It requests a small window ending at each selected
contract expiry and reports whether the provider returned actual bars.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from app.algo.auth import AngelOneAuth
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog
from app.backtesting.historical_ingest import HistoricalFetchRequest, HistoricalIngestionService
from app.backtesting.angelone_historical import AngelOneHistoricalSource

IST = ZoneInfo("Asia/Kolkata")


def _market_ns(day: date, *, end: bool) -> int:
    value = time(15, 30) if end else time(9, 15)
    return int(datetime.combine(day, value, tzinfo=IST).timestamp() * 1_000_000_000)


def select_expired_stock_futures(
    records: list[ContractRecord] | tuple[ContractRecord, ...],
    *,
    today: date,
    max_contracts: int = 20,
) -> tuple[ContractRecord, ...]:
    """Select real expired NFO stock-future records from historical snapshots."""
    if max_contracts < 1:
        raise ValueError("max_contracts must be positive")
    unique: dict[tuple[str, str, date], ContractRecord] = {}
    for record in records:
        if record.exchange.upper() != "NFO":
            continue
        if record.instrument_type.upper() != "STOCK_FUTURE":
            continue
        if not record.underlying or not record.token or record.expiry >= today:
            continue
        key = (record.underlying.upper(), str(record.token), record.expiry)
        unique.setdefault(key, record)
    return tuple(
        sorted(
            unique.values(),
            key=lambda r: (r.expiry, r.underlying.upper(), str(r.token)),
            reverse=True,
        )[:max_contracts]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-around-expiry", type=int, default=5)
    parser.add_argument("--max-contracts", type=int, default=20)
    parser.add_argument("--db", default="backtest_market_data.sqlite3")
    parser.add_argument("--contract-db", default="backtest_contract_master.sqlite3")
    args = parser.parse_args()
    if args.days_around_expiry < 1:
        raise SystemExit("days-around-expiry must be positive")

    today = datetime.now(IST).date()
    contracts = ContractMasterCatalog(args.contract_db)
    history = HistoricalCatalog(args.db)
    try:
        snapshots = contracts.snapshot_dates()
        if not snapshots:
            raise SystemExit("NO_HISTORICAL_CONTRACT_SNAPSHOTS")
        records = tuple(
            record
            for snapshot in snapshots
            for record in contracts.all_contracts(snapshot_date=snapshot)
        )
        selected = select_expired_stock_futures(
            records, today=today, max_contracts=args.max_contracts
        )
        if not selected:
            raise SystemExit("NO_EXPIRED_STOCK_FUTURES_IN_HISTORICAL_SNAPSHOTS")

        auth = AngelOneAuth()
        auth.get_client()
        source = AngelOneHistoricalSource(auth=auth, chunk_days=min(args.days_around_expiry, 30))
        ingestion = HistoricalIngestionService(history)

        print(f"PROBE_DATE={today.isoformat()}")
        print(f"HISTORICAL_SNAPSHOT_COUNT={len(snapshots)}")
        print(f"EXPIRED_CONTRACT_COUNT={len(selected)}")

        successful = 0
        for contract in selected:
            start_day = contract.expiry - timedelta(days=args.days_around_expiry - 1)
            instrument = f"NFO:{contract.token}:{contract.symbol}"
            request = HistoricalFetchRequest(
                source="angelone",
                instrument=instrument,
                timeframe="1m",
                start_ns=_market_ns(start_day, end=False),
                end_ns=_market_ns(contract.expiry, end=True),
            )
            print(
                f"EXPIRED_FUTURE_START underlying={contract.underlying} "
                f"expiry={contract.expiry.isoformat()} token={contract.token}"
            )
            try:
                result = ingestion.sync_streaming(source, request, batch_size=1024)
                print(
                    f"EXPIRED_FUTURE_RESULT underlying={contract.underlying} "
                    f"expiry={contract.expiry.isoformat()} token={contract.token} "
                    f"fetched={result.fetched} inserted={result.inserted}"
                )
                if result.fetched:
                    successful += 1
                    print(
                        f"EXPIRED_FUTURE_AVAILABLE underlying={contract.underlying} "
                        f"expiry={contract.expiry.isoformat()} token={contract.token}"
                    )
                else:
                    print(
                        f"EXPIRED_FUTURE_UNAVAILABLE underlying={contract.underlying} "
                        f"expiry={contract.expiry.isoformat()} token={contract.token}"
                    )
            except Exception as exc:
                print(
                    f"EXPIRED_FUTURE_ERROR underlying={contract.underlying} "
                    f"expiry={contract.expiry.isoformat()} token={contract.token} "
                    f"error={exc}"
                )

        print(f"EXPIRED_FUTURE_AVAILABLE_COUNT={successful}")
        print("EXPIRED_FUTURE_HISTORY_PROBE=COMPLETE")
        return 0 if successful else 2
    finally:
        history.close()
        contracts.close()


if __name__ == "__main__":
    raise SystemExit(main())
