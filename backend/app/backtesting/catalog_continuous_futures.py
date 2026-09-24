"""Build continuous futures from contract-master history and durable market data."""

from __future__ import annotations

from datetime import date

from .contract_master import ContractMasterCatalog, ContractRecord
from .continuous_futures import ContinuousFuturesRecord, build_continuous_futures_series_from_catalog
from .fno_rollover import FNORolloverWindow
from .historical_catalog import HistoricalCatalog


def build_continuous_futures_from_catalog(
    contract_catalog: ContractMasterCatalog,
    historical_catalog: HistoricalCatalog,
    *,
    underlying: str,
    start_date: date,
    end_date: date,
    source: str = "angelone",
    timeframe: str = "1m",
    exchange: str = "NFO",
    instrument_type: str = "STOCK_FUTURE",
    instrument_prefix: str | None = None,
) -> tuple[ContinuousFuturesRecord, ...]:
    """Resolve a real historical contract chain and project its stored bars.

    Contract-master snapshots provide contract identity and expiry. The
    HistoricalCatalog provides raw bars. No synthetic bars are generated and
    the underlying catalog records are never modified.
    """
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if not underlying.strip():
        raise ValueError("underlying is required")
    if not source.strip() or not timeframe.strip() or not exchange.strip() or not instrument_type.strip():
        raise ValueError("source, timeframe, exchange and instrument_type are required")

    # Keep every snapshot version. Resolving the whole period through the
    # generic rollover builder would reject contracts introduced after the
    # replay start date, which is valid for later point-in-time dates.
    snapshot_dates = tuple(
        snapshot_date
        for snapshot_date in contract_catalog.snapshot_dates()
        if snapshot_date <= end_date
    )
    contracts_by_snapshot: dict[date, tuple[ContractRecord, ...]] = {
        snapshot_date: tuple(
            contract
            for contract in contract_catalog.all_contracts(snapshot_date=snapshot_date)
            if (
                contract.exchange == exchange
                and contract.underlying == underlying.upper()
                and contract.instrument_type == instrument_type
                and contract.expiry >= start_date
            )
        )
        for snapshot_date in snapshot_dates
    }
    if not any(contracts_by_snapshot.values()):
        raise LookupError(f"no contract-master history for {underlying} from {start_date} to {end_date}")

    # Resolve the nearest non-expired contract using the latest snapshot
    # available on each replay date. This preserves point-in-time semantics.
    contracts_by_token: dict[str, ContractRecord] = {}
    windows: list[FNORolloverWindow] = []
    cursor = start_date
    while cursor <= end_date:
        for snapshot_date in snapshot_dates:
            if snapshot_date > cursor:
                break
            for contract in contracts_by_snapshot[snapshot_date]:
                current = contracts_by_token.get(contract.token)
                if current is None or (
                    current.snapshot_date is not None
                    and contract.snapshot_date is not None
                    and contract.snapshot_date > current.snapshot_date
                ):
                    contracts_by_token[contract.token] = contract

        eligible = tuple(
            sorted(
                (
                    contract
                    for contract in contracts_by_token.values()
                    if contract.expiry >= cursor
                    and (contract.snapshot_date is None or contract.snapshot_date <= cursor)
                ),
                key=lambda contract: (contract.expiry, contract.token),
            )
        )
        active = eligible[0] if eligible else None
        if active is not None:
            window_end = min(end_date, active.expiry)
            if windows and windows[-1].contract_token == active.token:
                windows[-1] = FNORolloverWindow(
                    windows[-1].underlying,
                    windows[-1].instrument_type,
                    windows[-1].contract_token,
                    windows[-1].start_date,
                    window_end,
                )
            else:
                windows.append(
                    FNORolloverWindow(
                        underlying.upper(), instrument_type, active.token, cursor, window_end
                    )
                )
        cursor += date.resolution

    windows = tuple(windows)
    if not windows:
        raise LookupError(f"no contract-master history for {underlying} from {start_date} to {end_date}")

    return build_continuous_futures_series_from_catalog(
        historical_catalog,
        windows,
        source=source,
        timeframe=timeframe,
        instrument_prefix=instrument_prefix or f"{exchange}:",
    )


__all__ = ["build_continuous_futures_from_catalog"]
