"""Read-only account-scoped reconciliation with durable report evidence."""
from __future__ import annotations

import hashlib
import json
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.brokers.order_status import normalize_order_status
from app.execution.broker_reconciliation import (
    BrokerSnapshot,
    ExpectedOrder,
    ReconciliationIssue,
    ReconciliationReport,
    ReconciliationStatus,
    reconcile_snapshot,
)
from app.models.broker_reconciliation import BrokerReconciliationRecord
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord,
    ExecutionFillRecord,
    ExecutionIntentRecord,
    ExecutionLegRecord,
)


class ReconciliationIdentityConflict(ValueError):
    """A reconciliation ID was reused for a different broker snapshot/report."""


def _transaction(db: Session):
    """Preserve caller transactions and force a physical BEGIN on SQLite."""
    if db.in_transaction():
        if db.get_bind().dialect.name == "sqlite":
            connection = db.connection()
            raw_connection = connection.connection.driver_connection
            if not raw_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
        return db.begin_nested()
    transaction = db.begin()
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        raw_connection = connection.connection.driver_connection
        if not raw_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    return transaction


def _report_payload(report: ReconciliationReport) -> dict:
    return {
        "account_id": report.account_id,
        "broker": report.broker,
        "snapshot_complete": report.snapshot_complete,
        "matched_order_count": report.matched_order_count,
        "is_consistent": report.is_consistent,
        "issues": [
            {"status": issue.status.value, "identity": issue.identity, "detail": issue.detail}
            for issue in report.issues
        ],
    }


def reconcile_persisted_account_snapshot(
    db: Session,
    *,
    snapshot: BrokerSnapshot,
    reconciliation_id: str,
    intent_ids: tuple[str, ...],
) -> dict:
    """Compare a supplied complete-window broker snapshot with persisted evidence.

    intent_ids explicitly scope the local order/fill ledger to the same history
    window represented by the broker snapshot. The function never calls a broker,
    submits/cancels orders, changes execution intents, or authorizes Live trading.
    """
    reconciliation_id = reconciliation_id.strip()
    intent_ids = tuple(dict.fromkeys(x.strip() for x in intent_ids if x and x.strip()))
    if not reconciliation_id:
        raise ValueError("reconciliation_id is required")
    if not intent_ids:
        raise ValueError("at least one history-window-scoped intent_id is required")

    broker = snapshot.broker.strip().lower()
    account_id = snapshot.account_id.strip()
    with _transaction(db):
        intents = db.execute(select(ExecutionIntentRecord).where(
            ExecutionIntentRecord.account_id == account_id,
            ExecutionIntentRecord.intent_id.in_(intent_ids),
        )).scalars().all()
        found_ids = {row.intent_id for row in intents}
        if found_ids != set(intent_ids):
            raise LookupError("one or more execution intents do not belong to this broker account")

        orders = db.execute(
            select(ExecutionBrokerOrderRecord, ExecutionIntentRecord, ExecutionLegRecord)
            .join(ExecutionIntentRecord, ExecutionIntentRecord.id == ExecutionBrokerOrderRecord.intent_record_id)
            .join(ExecutionLegRecord, ExecutionLegRecord.id == ExecutionBrokerOrderRecord.leg_record_id)
            .where(
                ExecutionBrokerOrderRecord.account_id == account_id,
                ExecutionBrokerOrderRecord.broker == broker,
                ExecutionBrokerOrderRecord.intent_record_id.in_([row.id for row in intents]),
            )
            .order_by(ExecutionBrokerOrderRecord.id)
        ).all()
        expected = tuple(ExpectedOrder(
            leg_id=leg.leg_id,
            broker_order_id=order.broker_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
        ) for order, _intent, leg in orders)
        order_ids = {order.broker_order_id for order, _intent, _leg in orders}
        persisted_legs = db.execute(select(ExecutionLegRecord).where(ExecutionLegRecord.intent_record_id.in_([row.id for row in intents]))).scalars().all()
        recorded_leg_ids = {leg.id for _order, _intent, leg in orders}
        missing_order_legs = [leg for leg in persisted_legs if leg.id not in recorded_leg_ids]
        fills = db.execute(select(ExecutionFillRecord).where(
            ExecutionFillRecord.account_id == account_id,
            ExecutionFillRecord.broker == broker,
            ExecutionFillRecord.broker_order_id.in_(order_ids) if order_ids else False,
        )).scalars().all()
        local_fills = {fill.broker_fill_id: fill.quantity for fill in fills}
        local_fill_evidence = {
            fill.broker_fill_id: (fill.quantity, fill.fill_price, fill.occurred_at_ns)
            for fill in fills
        }

        report = reconcile_snapshot(
            account_id=account_id,
            broker=broker,
            expected_orders=expected,
            local_fill_quantities=local_fills,
            snapshot=snapshot,
            local_fill_evidence=local_fill_evidence,
        )
        # Preserve and classify provider status evidence alongside immutable
        # order/trade identities. Unknown/conflicting statuses become explicit
        # reconciliation issues; this never authorizes a retry or order action.
        status_evidence = tuple(
            normalize_order_status(
                broker=broker,
                raw_status=order.raw_status,
                quantity=order.quantity,
                filled_quantity=order.filled_quantity,
            )
            for order in snapshot.orders
        )
        status_issues = tuple(
            ReconciliationIssue(
                ReconciliationStatus.ORDER_STATUS_CONFLICT
                if item.status_conflict
                else ReconciliationStatus.ORDER_STATUS_REQUIRES_RECONCILIATION,
                order.broker_order_id,
                f"Provider status {item.raw_status!r} normalized as {item.state.value}; "
                f"filled={item.filled_quantity}, remaining={item.remaining_quantity}.",
            )
            for order, item in zip(snapshot.orders, status_evidence)
            if item.requires_reconciliation
        )
        if status_issues:
            report = ReconciliationReport(
                account_id=report.account_id,
                broker=report.broker,
                snapshot_complete=report.snapshot_complete,
                matched_order_count=report.matched_order_count,
                issues=report.issues + status_issues,
            )
        if missing_order_legs:
            report = ReconciliationReport(
                account_id=report.account_id,
                broker=report.broker,
                snapshot_complete=report.snapshot_complete,
                matched_order_count=report.matched_order_count,
                issues=report.issues + tuple(
                    ReconciliationIssue(
                        ReconciliationStatus.MISSING_PERSISTED_BROKER_ORDER_ID,
                        f"{intent.intent_id}:{leg.leg_id}",
                        "Persisted execution leg has no broker order identity for this broker/account.",
                    )
                    for intent in intents
                    for leg in missing_order_legs
                    if leg.intent_record_id == intent.id
                ),
            )
        # A multi-leg Box Spread is not complete unless every persisted leg has
        # the same broker-confirmed cumulative filled quantity. This is a
        # read-only safety classification; it never repairs or balances legs.
        snapshot_orders_by_id = {
            row.broker_order_id: row for row in snapshot.orders
        }
        multi_leg_issues = []
        for intent in intents:
            intent_legs = [
                leg for leg in persisted_legs
                if leg.intent_record_id == intent.id
            ]
            if len(intent_legs) < 2:
                continue
            leg_fill_quantities = {}
            for leg in intent_legs:
                matching_order = next(
                    (
                        order for order, _owner, order_leg in orders
                        if order_leg.id == leg.id
                    ),
                    None,
                )
                if matching_order is None:
                    continue
                broker_order = snapshot_orders_by_id.get(
                    matching_order.broker_order_id
                )
                if broker_order is not None:
                    leg_fill_quantities[leg.leg_id] = broker_order.filled_quantity
            if len(leg_fill_quantities) >= 2 and len(set(leg_fill_quantities.values())) > 1:
                quantities = ", ".join(
                    f"{leg_id}={quantity}"
                    for leg_id, quantity in sorted(leg_fill_quantities.items())
                )
                multi_leg_issues.append(ReconciliationIssue(
                    ReconciliationStatus.MULTI_LEG_FILL_QUANTITY_MISMATCH,
                    intent.intent_id,
                    "Broker-confirmed cumulative fill quantities differ across "
                    f"execution legs ({quantities}); the spread is incomplete "
                    "and requires operator reconciliation.",
                ))
        if multi_leg_issues:
            report = ReconciliationReport(
                account_id=report.account_id,
                broker=report.broker,
                snapshot_complete=report.snapshot_complete,
                matched_order_count=report.matched_order_count,
                issues=report.issues + tuple(multi_leg_issues),
            )
        payload = {
            "report": _report_payload(report),
            "intent_ids": list(intent_ids),
            "order_status_evidence": [
                {
                    "raw_status": item.raw_status,
                    "normalized_state": item.state.value,
                    "quantity": item.quantity,
                    "filled_quantity": item.filled_quantity,
                    "remaining_quantity": item.remaining_quantity,
                    "terminal": item.terminal,
                    "requires_reconciliation": item.requires_reconciliation,
                    "status_conflict": item.status_conflict,
                }
                for item in status_evidence
            ],
            "source_snapshot": {
                "account_id": snapshot.account_id,
                "broker": snapshot.broker.strip().lower(),
                "complete": snapshot.complete,
                "orders": [
                    {
                        "broker_order_id": row.broker_order_id,
                        "symbol": row.symbol,
                        "side": row.side,
                        "quantity": row.quantity,
                        "filled_quantity": row.filled_quantity,
                        "raw_status": row.raw_status,
                    }
                    for row in snapshot.orders
                ],
                "trades": [
                    {
                        "broker_trade_id": row.broker_trade_id,
                        "broker_order_id": row.broker_order_id,
                        "quantity": row.quantity,
                        "fill_price": row.fill_price,
                        "occurred_at_ns": row.occurred_at_ns,
                    }
                    for row in snapshot.trades
                ],
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        existing = db.execute(select(BrokerReconciliationRecord).where(
            BrokerReconciliationRecord.broker == broker,
            BrokerReconciliationRecord.account_id == account_id,
            BrokerReconciliationRecord.reconciliation_id == reconciliation_id,
        )).scalar_one_or_none()
        if existing is not None:
            if existing.payload_hash != digest:
                raise ReconciliationIdentityConflict(
                    "reconciliation identity was reused with a different report payload"
                )
            return {"created": False, "record_id": existing.id, "report": report}

        record = BrokerReconciliationRecord(
            broker=broker,
            account_id=account_id,
            reconciliation_id=reconciliation_id,
            snapshot_complete=report.snapshot_complete,
            is_consistent=report.is_consistent,
            matched_order_count=report.matched_order_count,
            issue_count=len(report.issues),
            created_at_ns=time.time_ns(),
            payload_hash=digest,
            report_json=canonical,
        )
        try:
            with db.begin_nested():
                db.add(record)
                db.flush()
        except IntegrityError:
            winner = db.execute(select(BrokerReconciliationRecord).where(
                BrokerReconciliationRecord.broker == broker,
                BrokerReconciliationRecord.account_id == account_id,
                BrokerReconciliationRecord.reconciliation_id == reconciliation_id,
            )).scalar_one_or_none()
            if winner is None:
                raise
            if winner.payload_hash != digest:
                raise ReconciliationIdentityConflict(
                    "reconciliation identity was reused with a different report payload"
                )
            return {"created": False, "record_id": winner.id, "report": report}

        return {"created": True, "record_id": record.id, "report": report}
