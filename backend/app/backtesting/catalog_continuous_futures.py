"""Build continuous futures directly from durable catalog data."""

from __future__ import annotations

from datetime import date

from .contract_master import ContractMasterCatalog, ContractRecord
from .continuous_futures import ContinuousFuturesRecord, build_continuous_futures_series
from .fno_rollover import build_futures_rollover_chain
from .historical_catalog import HistoricalCatalog


def build_continuous_futures_from_catalog(
    contract_catalog: ContractMasterCatalog,
    historical_catalog: HistoricalCatalog,
    *,
    underlying: str,
    start_date: date,
    end_date: date,
    exchange: str = "NFO",
    instrument_type: str = "STOCK_FUTURE",
    source: str = "angelone",
    timeframe: str = "1m",
) -> tuple[ContinuousFuturesRecord, ...]:
    """Resolve real contracts and project only their durable raw history."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if not underlying.strip():
        raise ValueError("underlying is required")
    if not source.strip() or not timeframe.strip():
        raise ValueError("source and timeframe are required")

    contracts_by_token: dict[str, ContractRecord] = {}
    for snapshot_date in contract_catalog.snapshot_dates():
        if snapshot_date > end_date:
            break
        for contract in contract_catalog.all_contracts(snapshot_date=snapshot_date):
            if (
                contract.exchange == exchange
                and contract.underlying == underlying.upper()
                and contract.instrument_type == instrument_type
                and contract.expiry >= start_date
            ):
                contracts_by_token.setdefault(contract.token, contract)

    contracts = tuple(sorted(contracts_by_token.values(), key=lambda item: (item.expiry, item.token)))
    if not contracts:
        raise LookupError(f"no contract-master history for {underlying} from {start_date} to {end_date}")

    windows = build_futures_rollover_chain(
        contracts,
        underlying=underlying.upper(),
        instrument_type=instrument_type,
        start_date=start_date,
        end_date=end_date,
    )
    records_by_token = historical_catalog.records_by_contract_tokens(
        source=source,
        contract_tokens=(contract.token for contract in contracts),
        timeframe=timeframe,
        instrument_prefix=f"{exchange}:",
    )
    return build_continuous_futures_series(windows, records_by_token)


__all__ = ["build_continuous_futures_from_catalog"]
