"""Durable daily Angel One contract-master snapshot synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .angelone_contract_master import AngelOneContractMasterSource
from .contract_master import ContractMasterCatalog


@dataclass(frozen=True)
class ContractMasterSyncResult:
    snapshot_date: date
    records: int
    skipped: bool = False


class DailyContractMasterSync:
    """Capture one dated provider snapshot and never fabricate historical contracts."""

    def __init__(self, catalog: ContractMasterCatalog, source: AngelOneContractMasterSource | None = None) -> None:
        self.catalog = catalog
        self.source = source or AngelOneContractMasterSource()

    def sync(self, *, snapshot_date: date | None = None, force: bool = False) -> ContractMasterSyncResult:
        target = snapshot_date or self.source.market_date()
        if not force and target in self.catalog.snapshot_dates():
            return ContractMasterSyncResult(target, 0, skipped=True)
        records = self.source.sync(self.catalog, snapshot_date=target)
        if records <= 0:
            raise ValueError(f"Angel One contract master returned no stock futures for {target.isoformat()}")
        return ContractMasterSyncResult(target, records)
