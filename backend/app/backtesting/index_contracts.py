"""Dynamic index-futures contract discovery and historical resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .contract_master import ContractMasterCatalog, ContractRecord


INDEX_INSTRUMENT_TYPES = frozenset({"FUTIDX", "INDEX_FUTURE"})


@dataclass(frozen=True)
class IndexContract:
    """Resolved index future with stable provider identity and execution metadata."""

    exchange: str
    segment: str
    underlying: str
    token: str
    symbol: str
    expiry: date
    lot_size: int
    tick_size: float | None
    rank: str


class IndexContractMaster:
    """Normalize index futures from the provider master without hardcoded indices."""

    @staticmethod
    def _date(value: Any) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text.upper(), fmt).date()
            except ValueError:
                continue
        return None

    @classmethod
    def normalize(cls, rows: Iterable[Mapping[str, Any]]) -> tuple[ContractRecord, ...]:
        records: list[ContractRecord] = []
        for row in rows:
            segment = str(row.get("exch_seg", "")).strip().upper()
            instrument_type = str(row.get("instrumenttype", "")).strip().upper()
            if instrument_type not in INDEX_INSTRUMENT_TYPES:
                continue
            if not segment:
                continue
            expiry = cls._date(row.get("expiry"))
            token = str(row.get("token", "")).strip()
            symbol = str(row.get("symbol", "")).strip()
            underlying = str(row.get("name", "")).strip().upper()
            try:
                lot_size = int(str(row.get("lotsize", "0")).strip())
            except ValueError:
                continue
            tick_raw = str(row.get("tick_size", row.get("ticksize", ""))).strip()
            try:
                tick_size = float(tick_raw) if tick_raw else None
            except ValueError:
                tick_size = None
            if expiry is None or not token or not symbol or not underlying or lot_size <= 0:
                continue
            records.append(
                ContractRecord(
                    segment,
                    symbol,
                    token,
                    expiry,
                    "INDEX_FUTURE",
                    underlying,
                    lot_size,
                    tick_size=tick_size,
                )
            )
        return tuple(records)

    @staticmethod
    def resolve(
        catalog: ContractMasterCatalog,
        *,
        exchange: str,
        underlying: str,
        as_of: date,
        rank: str = "NEAR",
    ) -> IndexContract:
        rank = rank.upper()
        if rank not in {"NEAR", "NEXT", "FAR"}:
            raise ValueError("rank must be NEAR, NEXT or FAR")
        contracts = catalog.contracts(
            exchange=exchange,
            underlying=underlying.upper(),
            as_of=as_of,
            instrument_type="INDEX_FUTURE",
        )
        index = {"NEAR": 0, "NEXT": 1, "FAR": 2}[rank]
        if len(contracts) <= index:
            raise LookupError(
                f"no {rank.lower()} index future for {underlying} on {as_of.isoformat()}"
            )
        record = contracts[index]
        return IndexContract(
            exchange=record.exchange,
            segment=record.exchange,
            underlying=record.underlying,
            token=record.token,
            symbol=record.symbol,
            expiry=record.expiry,
            lot_size=record.lot_size,
            tick_size=record.tick_size,
            rank=rank,
        )
