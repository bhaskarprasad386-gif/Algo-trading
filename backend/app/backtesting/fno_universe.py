"""Dynamic F&O universe coverage derived from the canonical contract master.

The provider instrument master remains the source of truth: this module never
hardcodes an F&O stock/index list and never fabricates contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .contract_master import ContractMasterCatalog, ContractRecord


@dataclass(frozen=True)
class FNOUniverse:
    """Provider-backed F&O universe for one historical snapshot."""

    snapshot_date: date
    stock_underlyings: tuple[str, ...]
    index_underlyings: tuple[str, ...]
    stock_contracts: tuple[ContractRecord, ...]
    index_contracts: tuple[ContractRecord, ...]

    @property
    def underlyings(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.stock_underlyings + self.index_underlyings))

    @property
    def contract_count(self) -> int:
        return len(self.stock_contracts) + len(self.index_contracts)


def build_fno_universe(
    records: Iterable[ContractRecord], *, snapshot_date: date
) -> FNOUniverse:
    """Build a deterministic universe from provider-normalized contracts."""
    stock = tuple(
        r for r in records
        if r.instrument_type == "STOCK_FUTURE"
    )
    index = tuple(
        r for r in records
        if r.instrument_type == "INDEX_FUTURE"
    )
    stock_underlyings = tuple(dict.fromkeys(r.underlying for r in stock))
    index_underlyings = tuple(dict.fromkeys(r.underlying for r in index))
    return FNOUniverse(
        snapshot_date=snapshot_date,
        stock_underlyings=tuple(sorted(stock_underlyings)),
        index_underlyings=tuple(sorted(index_underlyings)),
        stock_contracts=stock,
        index_contracts=index,
    )


def load_fno_universe(
    catalog: ContractMasterCatalog, *, as_of: date
) -> FNOUniverse:
    """Load every stock/index future from the latest snapshot available by ``as_of``."""
    snapshot = catalog.latest_snapshot_date()
    if snapshot is None or snapshot > as_of:
        dates = [d for d in catalog.snapshot_dates() if d <= as_of]
        if not dates:
            raise LookupError(f"no F&O contract-master snapshot for {as_of.isoformat()}")
        snapshot = dates[-1]

    # Read all rows through the catalog's public resolver so callers do not
    # depend on SQLite internals. Discover underlyings from the snapshot by
    # querying the two canonical instrument types via a small catalog helper.
    records = catalog.all_contracts(snapshot_date=snapshot)
    return build_fno_universe(records, snapshot_date=snapshot)


__all__ = ["FNOUniverse", "build_fno_universe", "load_fno_universe"]
