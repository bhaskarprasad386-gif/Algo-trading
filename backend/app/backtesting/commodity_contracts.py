"""Angel One commodity-futures contract normalization."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .contract_master import ContractRecord

COMMODITY_INSTRUMENT_TYPES = frozenset({"FUTCOM", "FUTCOMINDEX"})


class CommodityContractMaster:
    """Normalize all provider commodity futures without a hardcoded commodity list."""

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
            if instrument_type not in COMMODITY_INSTRUMENT_TYPES or not segment:
                continue
            expiry = cls._date(row.get("expiry"))
            token = str(row.get("token", "")).strip()
            symbol = str(row.get("symbol", "")).strip()
            underlying = str(row.get("name", "")).strip().upper()
            try:
                lot_size = int(str(row.get("lotsize", "0")).strip())
            except ValueError:
                continue
            if expiry is None or not token or not symbol or not underlying or lot_size <= 0:
                continue
            records.append(
                ContractRecord(
                    segment, symbol, token, expiry, "COMMODITY_FUTURE",
                    underlying, lot_size,
                )
            )
        return tuple(records)


__all__ = ["COMMODITY_INSTRUMENT_TYPES", "CommodityContractMaster"]
