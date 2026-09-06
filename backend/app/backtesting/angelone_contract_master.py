"""Angel One instrument-master ingestion for historical F&O token resolution.

Angel One publishes a daily consolidated instrument master. This adapter imports
that real provider data into the local contract catalog; it never invents tokens.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

import requests

from .contract_master import ContractMasterCatalog, ContractRecord

ANGEL_ONE_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


class AngelOneContractMasterSource:
    """Fetch and normalize Angel One's published instrument master."""

    def __init__(self, *, url: str = ANGEL_ONE_MASTER_URL, timeout_seconds: float = 30.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def fetch(self) -> tuple[Mapping[str, Any], ...]:
        response = requests.get(self.url, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Angel One instrument master must be a JSON list")
        return tuple(row for row in payload if isinstance(row, dict))

    @staticmethod
    def _expiry(value: Any) -> date | None:
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
    def normalize_futures(cls, rows: Iterable[Mapping[str, Any]]) -> tuple[ContractRecord, ...]:
        records: list[ContractRecord] = []
        for row in rows:
            exchange = str(row.get("exch_seg", "")).upper()
            instrument_type = str(row.get("instrumenttype", "")).upper()
            if exchange != "NFO" or instrument_type != "FUTSTK":
                continue
            expiry = cls._expiry(row.get("expiry"))
            token = str(row.get("token", "")).strip()
            symbol = str(row.get("symbol", "")).strip()
            underlying = str(row.get("name", "")).strip().upper()
            try:
                lot_size = int(str(row.get("lotsize", "0")))
            except ValueError:
                continue
            if expiry is None or not token or not symbol or not underlying or lot_size <= 0:
                continue
            records.append(
                ContractRecord(
                    exchange=exchange,
                    symbol=symbol,
                    token=token,
                    expiry=expiry,
                    instrument_type="STOCK_FUTURE",
                    underlying=underlying,
                    lot_size=lot_size,
                )
            )
        return tuple(records)

    def sync(self, catalog: ContractMasterCatalog) -> int:
        return catalog.upsert(self.normalize_futures(self.fetch()))
