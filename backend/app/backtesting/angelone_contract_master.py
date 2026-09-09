"""Angel One instrument-master ingestion with dated snapshot retention."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import requests

from .contract_master import ContractMasterCatalog, ContractRecord
from .index_contracts import IndexContractMaster

ANGEL_ONE_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


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
    def market_date(now: datetime | None = None) -> date:
        """Return the provider snapshot date in the NSE/Angel One market timezone."""
        current = now or datetime.now(MARKET_TIMEZONE)
        if current.tzinfo is None:
            current = current.replace(tzinfo=MARKET_TIMEZONE)
        return current.astimezone(MARKET_TIMEZONE).date()

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
            if str(row.get("exch_seg", "")).upper() != "NFO":
                continue
            if str(row.get("instrumenttype", "")).upper() != "FUTSTK":
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
            records.append(ContractRecord("NFO", symbol, token, expiry, "STOCK_FUTURE", underlying, lot_size))
        return tuple(records)

    @staticmethod
    def normalize_index_futures(rows: Iterable[Mapping[str, Any]]) -> tuple[ContractRecord, ...]:
        """Return all provider index futures, across supported exchange segments."""
        return IndexContractMaster.normalize(rows)

    def sync(self, catalog: ContractMasterCatalog, *, snapshot_date: date | None = None) -> int:
        rows = self.fetch()
        snapshot = snapshot_date or self.market_date()
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        return catalog.upsert_snapshot(snapshot, self.normalize_futures(rows), payload_sha256=digest, fetched_at=datetime.now(MARKET_TIMEZONE).replace(tzinfo=None))

    def sync_index_futures(self, catalog: ContractMasterCatalog, *, snapshot_date: date | None = None) -> int:
        """Merge the dynamically discovered index-futures universe into a snapshot."""
        rows = self.fetch()
        snapshot = snapshot_date or self.market_date()
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        return catalog.merge_snapshot(snapshot, self.normalize_index_futures(rows), payload_sha256=digest, fetched_at=datetime.now(MARKET_TIMEZONE).replace(tzinfo=None))
