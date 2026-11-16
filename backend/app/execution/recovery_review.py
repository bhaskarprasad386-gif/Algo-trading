"""Read-only per-intent broker recovery review.

This module never mutates execution state or authorizes order replay/resume.
Account-wide broker books are narrowed to one persisted intent before the
pure reconciliation contract is evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.execution.broker_reconciliation import (
    BrokerSnapshot,
    ExpectedOrder,
    ReconciliationIssue,
    ReconciliationStatus,
    reconcile_snapshot,
)
from app.execution.recovery import RecoveryCandidate


class RecoveryDisposition(str, Enum):
    BLOCKED = "BLOCKED"
    CONSISTENT_READ_ONLY = "CONSISTENT_READ_ONLY"


@dataclass(frozen=True)
class LocalBrokerOrder:
    leg_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: int


@dataclass(frozen=True)
class RecoveryReview:
    account_id: str
    broker: str
    intent_id: str
    disposition: RecoveryDisposition
    matched_order_count: int
    issues: tuple[ReconciliationIssue, ...]

    @property
    def is_consistent(self) -> bool:
        """Evidence consistency only; this is never execution authorization."""
        return self.disposition is RecoveryDisposition.CONSISTENT_READ_ONLY


def review_recovery_candidate(
    *,
    candidate: RecoveryCandidate,
    broker: str,
    snapshot: BrokerSnapshot,
    local_orders: tuple[LocalBrokerOrder, ...],
    local_fill_quantities: dict[str, int],
) -> RecoveryReview:
    """Review one persisted intent against a complete account-scoped snapshot.

    The caller supplies only this intent's immutable local order/fill ledger.
    Unrelated account orders and trades are excluded. A broker order that
    appears to match an untracked leg is reported for manual review, never
    silently adopted or replayed.
    """
    intent = candidate.persisted.intent
    account_id = intent.account_id
    if snapshot.account_id != account_id or snapshot.broker.strip().lower() != broker.strip().lower():
        raise ValueError("broker snapshot account/broker scope does not match recovery candidate")

    issues: list[ReconciliationIssue] = []
    leg_by_id = {leg.leg_id: leg for leg in candidate.persisted.legs}
    local_by_leg: dict[str, LocalBrokerOrder] = {}
    for order in local_orders:
        if order.leg_id in local_by_leg:
            issues.append(ReconciliationIssue(
                ReconciliationStatus.BROKER_ORDER_MISMATCH, order.leg_id,
                "More than one persisted broker order is linked to the same execution leg.",
            ))
        local_by_leg[order.leg_id] = order
        leg = leg_by_id.get(order.leg_id)
        if leg is None or (
            order.symbol != leg.instrument
            or order.side != leg.side.value
            or order.quantity != leg.quantity
        ):
            issues.append(ReconciliationIssue(
                ReconciliationStatus.BROKER_ORDER_MISMATCH, order.broker_order_id,
                "Persisted broker order does not match its execution leg identity or quantity.",
            ))

    expected: list[ExpectedOrder] = []
    known_order_ids: set[str] = set()
    for leg in candidate.persisted.legs:
        local = local_by_leg.get(leg.leg_id)
        if local is None:
            matches = [
                order for order in snapshot.orders
                if order.symbol == leg.instrument
                and order.side == leg.side.value
                and order.quantity == leg.quantity
                and order.broker_order_id not in known_order_ids
            ]
            detail = (
                "A broker order resembles this untracked leg; identity cannot be adopted automatically."
                if matches else
                "No persisted broker order identity exists for this execution leg."
            )
            issues.append(ReconciliationIssue(
                ReconciliationStatus.MISSING_BROKER_ORDER,
                leg.leg_id,
                detail,
            ))
            if matches:
                for match in matches:
                    issues.append(ReconciliationIssue(
                        ReconciliationStatus.BROKER_ORDER_MISMATCH,
                        match.broker_order_id,
                        "Potential untracked submission requires manual identity verification.",
                    ))
            continue
        known_order_ids.add(local.broker_order_id)
        expected.append(ExpectedOrder(
            leg_id=leg.leg_id,
            broker_order_id=local.broker_order_id,
            symbol=local.symbol,
            side=local.side,
            quantity=local.quantity,
        ))

    scoped_orders = tuple(
        order for order in snapshot.orders if order.broker_order_id in known_order_ids
    )
    scoped_trades = tuple(
        trade for trade in snapshot.trades if trade.broker_order_id in known_order_ids
    )
    scoped_snapshot = BrokerSnapshot(
        account_id=snapshot.account_id,
        broker=snapshot.broker,
        orders=scoped_orders,
        trades=scoped_trades,
        complete=snapshot.complete,
    )
    report = reconcile_snapshot(
        account_id=account_id,
        broker=broker,
        expected_orders=tuple(expected),
        local_fill_quantities=local_fill_quantities,
        snapshot=scoped_snapshot,
    )
    issues.extend(report.issues)
    disposition = (
        RecoveryDisposition.CONSISTENT_READ_ONLY
        if not issues and report.is_consistent
        else RecoveryDisposition.BLOCKED
    )
    return RecoveryReview(
        account_id=account_id,
        broker=broker.strip().lower(),
        intent_id=intent.intent_id,
        disposition=disposition,
        matched_order_count=report.matched_order_count,
        issues=tuple(issues),
    )
