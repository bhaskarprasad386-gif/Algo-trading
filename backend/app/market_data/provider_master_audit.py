"""Dry-run comparison of broker master coverage before snapshot publication."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .provider_master_parsers import MasterParseReport
from .provider_contracts import CanonicalContractKey, ContractMapping


@dataclass(frozen=True)
class MasterCoverageAudit:
    angel_contracts: int
    shoonya_contracts: int
    common_contracts: int
    angel_only: tuple[CanonicalContractKey, ...]
    shoonya_only: tuple[CanonicalContractKey, ...]
    metadata_mismatches: tuple[CanonicalContractKey, ...]
    duplicate_canonical_keys: tuple[tuple[str, CanonicalContractKey], ...]
    angel_only_ratio: float
    shoonya_only_ratio: float


def _index(report: MasterParseReport) -> tuple[dict[CanonicalContractKey, ContractMapping], tuple[CanonicalContractKey, ...]]:
    result: dict[CanonicalContractKey, ContractMapping] = {}
    duplicates: list[CanonicalContractKey] = []
    for mapping in report.mappings:
        if mapping.key in result:
            duplicates.append(mapping.key)
        else:
            result[mapping.key] = mapping
    return result, tuple(sorted(set(duplicates), key=_key_sort))


def _key_sort(key: CanonicalContractKey) -> tuple[str, ...]:
    return (
        key.exchange, key.segment, key.underlying, key.instrument_type,
        key.expiry.isoformat() if key.expiry else "",
        str(key.strike) if key.strike is not None else "",
        key.option_type or "",
    )


def audit_master_coverage(reports: Iterable[MasterParseReport]) -> MasterCoverageAudit:
    reports = tuple(reports)
    by_broker = {report.broker: report for report in reports}
    if len(reports) != 2 or set(by_broker) != {"ANGELONE", "SHOONYA"}:
        raise ValueError("coverage audit requires one Angel One and one Shoonya report")
    angel, angel_dupes = _index(by_broker["ANGELONE"])
    shoonya, shoonya_dupes = _index(by_broker["SHOONYA"])
    akeys, skeys = set(angel), set(shoonya)
    common = akeys & skeys
    mismatches = tuple(sorted(
        (key for key in common if
         angel[key].lot_size != shoonya[key].lot_size or
         angel[key].tick_size != shoonya[key].tick_size),
        key=_key_sort,
    ))
    angel_only = tuple(sorted(akeys - skeys, key=_key_sort))
    shoonya_only = tuple(sorted(skeys - akeys, key=_key_sort))
    dupes = tuple(
        [("ANGELONE", key) for key in angel_dupes] +
        [("SHOONYA", key) for key in shoonya_dupes]
    )
    return MasterCoverageAudit(
        angel_contracts=len(angel), shoonya_contracts=len(shoonya),
        common_contracts=len(common), angel_only=angel_only,
        shoonya_only=shoonya_only, metadata_mismatches=mismatches,
        duplicate_canonical_keys=dupes,
        angel_only_ratio=len(angel_only) / len(angel) if angel else 1.0,
        shoonya_only_ratio=len(shoonya_only) / len(shoonya) if shoonya else 1.0,
    )
