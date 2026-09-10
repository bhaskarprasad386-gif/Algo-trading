"""End-to-end historical acquisition and continuous-futures projection pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .catalog_continuous_futures import build_continuous_futures_from_catalog
from .contract_master import ContractMasterCatalog
from .continuous_futures import ContinuousFuturesRecord
from .continuous_futures_acquisition import (
    ContinuousFuturesAcquisitionReport,
    acquire_continuous_futures_history,
)
from .fno_rollover import FNORolloverWindow, build_futures_rollover_chain
from .historical_catalog import HistoricalCatalog
from .historical_ingest import HistoricalSource


@dataclass(frozen=True)
class ContinuousFuturesPipelineResult:
    windows: tuple[FNORolloverWindow, ...]
    acquisition: ContinuousFuturesAcquisitionReport
    series: tuple[ContinuousFuturesRecord, ...]


def run_continuous_futures_history_pipeline(
    contract_catalog: ContractMasterCatalog,
    historical_catalog: HistoricalCatalog,
    source: HistoricalSource,
    *,
    underlying: str,
    start_date: date,
    end_date: date,
    timeframe: str,
    interval_ns: int,
    calendar,
    max_request_ns: int,
    exchange: str = "NFO",
    instrument_type: str = "STOCK_FUTURE",
    executor=None,
) -> ContinuousFuturesPipelineResult:
    """Acquire missing active-contract history, then project the durable chain."""
    contracts_by_token = {}
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
    acquisition = acquire_continuous_futures_history(
        historical_catalog,
        source,
        windows,
        timeframe=timeframe,
        interval_ns=interval_ns,
        calendar=calendar,
        max_request_ns=max_request_ns,
        executor=executor,
    )
    series = build_continuous_futures_from_catalog(
        contract_catalog,
        historical_catalog,
        underlying=underlying,
        start_date=start_date,
        end_date=end_date,
        source=source.source_name,
        timeframe=timeframe,
        exchange=exchange,
        instrument_type=instrument_type,
    )
    return ContinuousFuturesPipelineResult(windows, acquisition, series)


__all__ = ["ContinuousFuturesPipelineResult", "run_continuous_futures_history_pipeline"]
