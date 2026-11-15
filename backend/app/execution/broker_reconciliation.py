"""Pure read-only comparison of persisted execution identities with broker snapshots.

This module classifies evidence only. It never calls a broker, writes the
database, changes an intent, retries an order, or enables Live execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    INCOMPLETE_SNAPSHOT = "INCOMPLETE_SNAPSHOT"
    MISSING_BROKER_ORDER = "MISSING_BROKER_ORDER"
    BROKER_ORDER_MISMATCH = "BROKER_ORDER_MISMATCH"
    UNRECORDED_BROKER_TRADE = "UNRECORDED_BROKER_TRADE"
    LOCAL_FILL_MISSING_AT_BROKER = "LOCAL_FILL_MISSING_AT_BROKER"
    FILL_QUANTITY_MISMATCH = "FILL_QUANTITY_MISMATCH"
    DUPLICATE_BROKER_ORDER_ID = "DUPLICATE_BROKER_ORDER_ID"
    DUPLICATE_BROKER_TRADE_ID = "DUPLICATE_BROKER_TRADE_ID"


@dataclass(frozen=True)
class ExpectedOrder:
    leg_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: int

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.leg_id, self.broker_order_id, self.symbol)):
            raise ValueError("leg_id, broker_order_id and symbol are required")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip() or not self.symbol.strip():
            raise ValueError("broker order identity and symbol are required")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.quantity <= 0 or not 0 <= self.filled_quantity <= self.quantity:
            raise ValueError("invalid broker order quantities")


@dataclass(frozen=True)
class BrokerTradeSnapshot:
    broker_trade_id: str
    broker_order_id: str
    quantity: int

    def __post_init__(self) -> None:
        if not self.broker_trade_id.strip() or not self.broker_order_id.strip():
            raise ValueError("broker trade and order identities are required")
        if self.quantity <= 0:
            raise ValueError("trade quantity must be positive")


@dataclass(frozen=True)
class ReconciliationIssue:
    status: ReconciliationStatus
    identity: str
    detail: str


@dataclass(frozen=True)
class ReconciliationReport:
    account_id: str
    broker: str
    snapshot_complete: bool
    matched_order_count: int
    issues: tuple[ReconciliationIssue, ...]

    @property
    def safe_to_resume(self) -> bool:
        # This is evidence classification only, never an authorization to trade.
        return self.snapshot_complete and not self.issues


def reconcile_snapshot(
    *,
    account_id: str,
    broker: str,
    expected_orders: tuple[ExpectedOrder, ...],
    local_fill_quantities: dict[str, int],
    broker_orders: tuple[BrokerOrderSnapshot, ...],
    broker_trades: tuple[BrokerTradeSnapshot, ...],
    snapshot_complete: bool,
) -> ReconciliationReport:
    """Compare a caller-supplied, account-scoped snapshot without side effects.

    local_fill_quantities maps persisted broker_trade_id -> persisted quantity.
    A caller must obtain a complete broker order AND trade history window before
    treating an empty result as authoritative. Any discrepancy blocks resume.
    """
    if not account_id.strip() or not broker.strip():
        raise ValueError("account_id and broker are required")
    if not isinstance(snapshot_complete, bool):
        raise ValueError("snapshot_complete must be a boolean")

    issues: list[ReconciliationIssue] = []
    if not snapshot_complete:
        issues.append(ReconciliationIssue(
            ReconciliationStatus.INCOMPLETE_SNAPSHOT, account_id,
            "Broker order/trade history is not confirmed complete; absence is not evidence.",
        ))

    expected_ids = [x.broker_order_id for x in expected_orders]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("expected broker_order_id values must be unique")
    broker_order_ids = [x.broker_order_id for x in broker_orders]
    duplicate_orders = {x for x in broker_order_ids if broker_order_ids.count(x) > 1}
    for identity in sorted(duplicate_orders):
        issues.append(ReconciliationIssue(
            ReconciliationStatus.DUPLICATE_BROKER_ORDER_ID, identity,
            "Broker snapshot contains the same order identity more than once.",
        ))
    broker_by_id = {x.broker_order_id: x for x in broker_orders}

    matched = 0
    for expected in expected_orders:
        actual = broker_by_id.get(expected.broker_order_id)
        if actual is None:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.MISSING_BROKER_ORDER, expected.broker_order_id,
                f"Persisted leg {expected.leg_id} has no matching broker order in the supplied snapshot.",
            ))
            continue
        if (
            actual.symbol != expected.symbol
            or actual.side != expected.side
            or actual.quantity != expected.quantity
        ):
            issues.append(ReconciliationIssue(
                ReconciliationStatus.BROKER_ORDER_MISMATCH, expected.broker_order_id,
                f"Persisted leg {expected.leg_id} differs from broker symbol/side/requested quantity.",
            ))
        else:
            matched += 1

    broker_trade_ids = [x.broker_trade_id for x in broker_trades]
    duplicate_trades = {x for x in broker_trade_ids if broker_trade_ids.count(x) > 1}
    for identity in sorted(duplicate_trades):
        issues.append(ReconciliationIssue(
            ReconciliationStatus.DUPLICATE_BROKER_TRADE_ID, identity,
            "Broker snapshot contains the same trade identity more than once.",
        ))

    broker_trade_map = {x.broker_trade_id: x for x in broker_trades}
    for trade_id, local_qty in local_fill_quantities.items():
        if not trade_id.strip() or local_qty <= 0:
            raise ValueError("local fill identities and quantities must be positive")
        broker_trade = broker_trade_map.get(trade_id)
        if broker_trade is None:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.LOCAL_FILL_MISSING_AT_BROKER, trade_id,
                "Persisted fill is absent from the supplied broker trade snapshot.",
            ))
        elif broker_trade.quantity != local_qty:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.FILL_QUANTITY_MISMATCH, trade_id,
                f"Persisted fill quantity {local_qty} differs from broker quantity {broker_trade.quantity}.",
            ))

    for trade in broker_trades:
        if trade.broker_trade_id not in local_fill_quantities:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.UNRECORDED_BROKER_TRADE, trade.broker_trade_id,
                "Broker reports a trade that is not present in the local immutable fill ledger.",
            ))

    return ReconciliationReport(
        account_id=account_id,
        broker=broker.strip().lower(),
        snapshot_complete=snapshot_complete,
        matched_order_count=matched,
        issues=tuple(issues),
    )
