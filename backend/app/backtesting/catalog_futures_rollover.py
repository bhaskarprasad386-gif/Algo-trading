"""Build Cash-Future rollover windows directly from the durable contract master."""

from __future__ import annotations

from datetime import date

from .contract_master import ContractMasterCatalog, ContractRecord
from .fno_rollover import FNORolloverWindow, build_futures_rollover_chain


def build_catalog_futures_rollover_windows(
    catalog: ContractMasterCatalog,
    *,
    underlying: str,
    start_date: date,
    end_date: date,
    instrument_type: str = "STOCK_FUTURE",
    exchange: str = "NFO",
    snapshot_date: date | None = None,
) -> tuple[FNORolloverWindow, ...]:
    """Resolve eligible real futures from the best available master snapshot."""
    if start_date > end_date:
        raise ValueError("start_date cannot exceed end_date")
    if not underlying.strip():
        raise ValueError("underlying is required")
    snapshot = snapshot_date or catalog.latest_snapshot_date()
    if snapshot is None:
        return ()
    contracts: tuple[ContractRecord, ...] = catalog.contracts(
        exchange=exchange,
        underlying=underlying.strip().upper(),
        as_of=start_date,
        instrument_type=instrument_type,
    )
    if snapshot < start_date:
        contracts = tuple(c for c in catalog.all_contracts(snapshot_date=snapshot) if c.exchange == exchange and c.underlying == underlying.strip().upper() and c.instrument_type == instrument_type and c.expiry >= start_date)
    return build_futures_rollover_chain(
        contracts,
        underlying=underlying.strip().upper(),
        instrument_type=instrument_type,
        start_date=start_date,
        end_date=end_date,
    )


def build_catalog_futures_rollover_windows_for_universe(
    catalog: ContractMasterCatalog,
    *,
    start_date: date,
    end_date: date,
    instrument_type: str = "STOCK_FUTURE",
    exchange: str = "NFO",
    snapshot_date: date | None = None,
) -> tuple[FNORolloverWindow, ...]:
    """Build deterministic rollover windows for every eligible underlying."""
    snapshot = snapshot_date or catalog.latest_snapshot_date()
    if snapshot is None:
        return ()
    underlyings = sorted({
        c.underlying for c in catalog.all_contracts(snapshot_date=snapshot)
        if c.exchange == exchange and c.instrument_type == instrument_type and c.expiry >= start_date
    })
    windows: list[FNORolloverWindow] = []
    for underlying in underlyings:
        windows.extend(build_catalog_futures_rollover_windows(
            catalog,
            underlying=underlying,
            start_date=start_date,
            end_date=end_date,
            instrument_type=instrument_type,
            exchange=exchange,
            snapshot_date=snapshot,
        ))
    return tuple(windows)


__all__ = [
    "build_catalog_futures_rollover_windows",
    "build_catalog_futures_rollover_windows_for_universe",
]
