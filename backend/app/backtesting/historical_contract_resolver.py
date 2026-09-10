"""Exact historical futures token resolution."""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass

from .contract_master import ContractMasterCatalog, ContractRecord


@dataclass(frozen=True)
class HistoricalFutureSelection:
    underlying: str
    contract_month: str
    record: ContractRecord

    @property
    def token(self) -> str:
        return self.record.token


class HistoricalContractResolver:
    def __init__(self, catalog: ContractMasterCatalog) -> None:
        self.catalog = catalog

    def resolve_future(self, *, underlying: str, contract_month: str, as_of: date) -> HistoricalFutureSelection:
        underlying = underlying.strip().upper()
        if not underlying:
            raise ValueError("underlying is required")
        record = self.catalog.resolve_contract_month(
            exchange="NFO",
            underlying=underlying,
            contract_month=contract_month,
            as_of=as_of,
            instrument_type="STOCK_FUTURE",
        )
        return HistoricalFutureSelection(underlying, contract_month, record)

    def resolve_future_token(self, *, underlying: str, contract_month: str, as_of: date) -> str:
        return self.resolve_future(underlying=underlying, contract_month=contract_month, as_of=as_of).token
