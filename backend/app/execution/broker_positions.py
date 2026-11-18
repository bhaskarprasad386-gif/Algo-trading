"""Typed, read-only broker position snapshot and reconciliation contract.

This capability is intentionally separate from order routing and order/trade
history. A complete empty snapshot may prove flat exposure; unavailable,
partial, stale, cross-account, or malformed evidence never does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum


@dataclass(frozen=True, order=True)
class PositionKey:
    exchange: str
    instrument: str
    product: str
    currency: str = "INR"

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.exchange, self.instrument, self.product, self.currency
        )):
            raise ValueError("position exchange, instrument, product and currency are required")


@dataclass(frozen=True)
class PositionQuantity:
    key: PositionKey
    net_quantity: int

    def __post_init__(self) -> None:
        if isinstance(self.net_quantity, bool) or not isinstance(self.net_quantity, int):
            raise ValueError("net_quantity must be an integer")


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    account_id: str
    broker: str
    trading_date: date
    fetched_at: datetime
    positions: tuple[PositionQuantity, ...]
    complete: bool
    pages_fetched: int = 1
    has_more: bool = False

    def __post_init__(self) -> None:
        if not self.account_id.strip() or not self.broker.strip():
            raise ValueError("position snapshot account_id and broker are required")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if not isinstance(self.complete, bool):
            raise ValueError("snapshot completeness must be a boolean")
        if self.pages_fetched < 1:
            raise ValueError("pages_fetched must be at least one")
        if self.complete and self.has_more:
            raise ValueError("a snapshot with more pages cannot be complete")


class PositionReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    INCOMPLETE_SNAPSHOT = "INCOMPLETE_SNAPSHOT"
    PAGINATION_INCOMPLETE = "PAGINATION_INCOMPLETE"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    FUTURE_SNAPSHOT = "FUTURE_SNAPSHOT"
    TRADING_DATE_MISMATCH = "TRADING_DATE_MISMATCH"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    BROKER_MISMATCH = "BROKER_MISMATCH"
    DUPLICATE_POSITION = "DUPLICATE_POSITION"
    UNTRACKED_BROKER_EXPOSURE = "UNTRACKED_BROKER_EXPOSURE"
    LOCAL_EXPOSURE_MISSING_AT_BROKER = "LOCAL_EXPOSURE_MISSING_AT_BROKER"
    NET_QUANTITY_MISMATCH = "NET_QUANTITY_MISMATCH"


@dataclass(frozen=True)
class PositionReconciliationIssue:
    status: PositionReconciliationStatus
    identity: str
    detail: str


@dataclass(frozen=True)
class PositionReconciliationReport:
    account_id: str
    broker: str
    snapshot_complete: bool
    issues: tuple[PositionReconciliationIssue, ...]

    @property
    def is_consistent_read_only(self) -> bool:
        """Evidence consistency only; never permission to resume or trade."""
        return self.snapshot_complete and not self.issues


def reconcile_positions(
    *,
    account_id: str,
    broker: str,
    trading_date: date,
    expected_positions: tuple[PositionQuantity, ...],
    snapshot: BrokerPositionSnapshot,
    now: datetime,
    max_age: timedelta = timedelta(seconds=30),
) -> PositionReconciliationReport:
    """Compare authoritative net positions with persisted expected exposure.

    Expected positions must be aggregated by exchange/instrument/product/
    currency before calling. Zero-quantity rows are ignored. An empty broker
    position list is authoritative only when the provider explicitly marks
    the snapshot complete and all scope/freshness checks pass.
    """
    if not account_id.strip() or not broker.strip():
        raise ValueError("account_id and broker are required")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_age.total_seconds() < 0:
        raise ValueError("max_age cannot be negative")

    issues: list[PositionReconciliationIssue] = []
    if snapshot.account_id != account_id:
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.ACCOUNT_MISMATCH, snapshot.account_id,
            "Broker positions belong to a different account.",
        ))
    if snapshot.broker.strip().lower() != broker.strip().lower():
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.BROKER_MISMATCH, snapshot.broker,
            "Broker positions belong to a different broker.",
        ))
    if snapshot.trading_date != trading_date:
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.TRADING_DATE_MISMATCH,
            snapshot.trading_date.isoformat(),
            "Broker position snapshot is not for the requested trading date.",
        ))
    age = now - snapshot.fetched_at
    if age < timedelta(0):
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.FUTURE_SNAPSHOT, account_id,
            "Broker position snapshot timestamp is in the future.",
        ))
    elif age > max_age:
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.STALE_SNAPSHOT, account_id,
            "Broker position snapshot exceeds the permitted freshness window.",
        ))
    if not snapshot.complete:
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.INCOMPLETE_SNAPSHOT, account_id,
            "Provider did not confirm a complete account positions snapshot; empty is not flat.",
        ))
    if snapshot.has_more:
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.PAGINATION_INCOMPLETE, account_id,
            "Broker position pagination has an unconsumed continuation.",
        ))

    expected_by_key: dict[PositionKey, int] = {}
    for item in expected_positions:
        expected_by_key[item.key] = expected_by_key.get(item.key, 0) + item.net_quantity
    actual_by_key: dict[PositionKey, int] = {}
    for item in snapshot.positions:
        if item.key in actual_by_key:
            issues.append(PositionReconciliationIssue(
                PositionReconciliationStatus.DUPLICATE_POSITION,
                repr(item.key),
                "Broker snapshot contains duplicate rows for the same position key.",
            ))
        actual_by_key[item.key] = actual_by_key.get(item.key, 0) + item.net_quantity

    expected_by_key = {key: qty for key, qty in expected_by_key.items() if qty != 0}
    actual_by_key = {key: qty for key, qty in actual_by_key.items() if qty != 0}
    for key in sorted(set(actual_by_key) - set(expected_by_key)):
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.UNTRACKED_BROKER_EXPOSURE, repr(key),
            f"Broker reports net quantity {actual_by_key[key]} with no matching persisted exposure.",
        ))
    for key in sorted(set(expected_by_key) - set(actual_by_key)):
        issues.append(PositionReconciliationIssue(
            PositionReconciliationStatus.LOCAL_EXPOSURE_MISSING_AT_BROKER, repr(key),
            f"Persisted exposure quantity {expected_by_key[key]} is absent from broker positions.",
        ))
    for key in sorted(set(expected_by_key) & set(actual_by_key)):
        if expected_by_key[key] != actual_by_key[key]:
            issues.append(PositionReconciliationIssue(
                PositionReconciliationStatus.NET_QUANTITY_MISMATCH, repr(key),
                f"Persisted net quantity {expected_by_key[key]} differs from broker net quantity {actual_by_key[key]}.",
            ))

    return PositionReconciliationReport(
        account_id=account_id,
        broker=broker.strip().lower(),
        snapshot_complete=snapshot.complete and not snapshot.has_more,
        issues=tuple(issues),
    )
