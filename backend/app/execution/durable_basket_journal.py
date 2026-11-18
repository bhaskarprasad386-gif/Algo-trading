"""SQLAlchemy-backed durable journal for the multi-leg basket coordinator.

This adapter persists coordinator intent/ack/reconciliation evidence only. It
never places broker orders, authorizes Live mode, or substitutes for the
separate emergency-recovery journal and broker-specific recovery adapter.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.execution.multi_leg_coordinator import (
    BasketReconciliation,
    BasketJournal,
    BrokerOrderAck,
)
from app.models.basket_execution_journal import (
    BasketExecutionJournalEventRecord,
    BasketExecutionJournalRecord,
)


class DurableBasketJournal:
    """Persist every coordinator gate before reporting it as successful.

    The unique (account_id, intent_id) constraint is the cross-worker claim
    arbiter. A second worker never takes over an existing claim, including an
    abandoned/unknown one; recovery must reconcile it explicitly.
    """

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    async def _call(self, operation, *args, **kwargs):
        return await asyncio.to_thread(operation, *args, **kwargs)

    def _append(self, db: Session, row: BasketExecutionJournalRecord,
                event_type: str, payload: dict) -> None:
        sequence = db.execute(
            select(func.coalesce(func.max(BasketExecutionJournalEventRecord.sequence), -1))
            .where(BasketExecutionJournalEventRecord.journal_id == row.id)
        ).scalar_one() + 1
        db.add(BasketExecutionJournalEventRecord(
            journal_id=row.id,
            sequence=sequence,
            event_type=event_type,
            payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
            occurred_at_ns=time.time_ns(),
        ))
        row.version += 1
        row.updated_at_ns = time.time_ns()

    def _mutate(self, *, account_id: str, intent_id: str, event_type: str,
                payload: dict, state: str | None = None,
                reason: str | None = None,
                client_order_ids: tuple[str, ...] | None = None,
                reconciliation: BasketReconciliation | None = None) -> None:
        with self._session_factory() as db, db.begin():
            row = db.execute(
                select(BasketExecutionJournalRecord).where(
                    BasketExecutionJournalRecord.account_id == account_id,
                    BasketExecutionJournalRecord.intent_id == intent_id,
                ).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise LookupError("durable basket submission claim not found")
            if state is not None:
                if row.state in {"PREFLIGHT_REJECTED", "FILLED"}:
                    raise ValueError(
                        f"terminal basket state {row.state} cannot transition to {state}"
                    )
                row.state = state
            if reason is not None:
                row.reason = reason
            if client_order_ids is not None:
                row.client_order_ids_json = json.dumps(list(client_order_ids))
            if reconciliation is not None:
                row.reconciliation_json = json.dumps({
                    "authoritative": reconciliation.authoritative,
                    "orders_complete": reconciliation.orders_complete,
                    "trades_complete": reconciliation.trades_complete,
                    "positions_reconciled": reconciliation.positions_reconciled,
                    "legs": [asdict(leg) for leg in reconciliation.legs],
                }, sort_keys=True, separators=(",", ":"))
            self._append(db, row, event_type, payload)

    async def claim_submission(self, *, account_id: str, intent_id: str,
                               strategy: str, snapshot_id: str) -> bool:
        return await self._call(
            self._claim_submission, account_id=account_id, intent_id=intent_id,
            strategy=strategy, snapshot_id=snapshot_id,
        )

    def _claim_submission(self, *, account_id: str, intent_id: str,
                          strategy: str, snapshot_id: str) -> bool:
        if not all(str(value).strip() for value in (account_id, intent_id, strategy, snapshot_id)):
            raise ValueError("account, intent, strategy and snapshot identities are required")
        now = time.time_ns()
        with self._session_factory() as db:
            try:
                with db.begin():
                    row = BasketExecutionJournalRecord(
                        account_id=account_id, intent_id=intent_id,
                        strategy=strategy, snapshot_id=snapshot_id,
                        state="CLAIMED", client_order_ids_json="[]",
                        reason="", version=0, created_at_ns=now, updated_at_ns=now,
                    )
                    db.add(row)
                    db.flush()
                    self._append(db, row, "SUBMISSION_CLAIMED", {
                        "strategy": strategy, "snapshot_id": snapshot_id,
                    })
                return True
            except IntegrityError:
                # A concurrent process already owns this intent. Never steal or
                # reinterpret a previous claim, even if its worker disappeared.
                return False

    async def record_submission_started(self, *, account_id: str, intent_id: str,
                                        client_order_ids: tuple[str, ...],
                                        expected_quantities: dict[str, int]) -> None:
        if not client_order_ids or len(set(client_order_ids)) != len(client_order_ids):
            raise ValueError("unique client order IDs are required for every basket leg")
        if (
            set(expected_quantities) != set(client_order_ids)
            or any(isinstance(q, bool) or not isinstance(q, int) or q <= 0
                   for q in expected_quantities.values())
        ):
            raise ValueError("positive expected quantities are required for every basket leg")
        await self._call(
            self._record_submission_started, account_id=account_id,
            intent_id=intent_id, client_order_ids=client_order_ids,
            expected_quantities=expected_quantities,
        )

    def _record_submission_started(self, *, account_id: str, intent_id: str,
                                   client_order_ids: tuple[str, ...],
                                   expected_quantities: dict[str, int]) -> None:
        with self._session_factory() as db, db.begin():
            row = db.execute(select(BasketExecutionJournalRecord).where(
                BasketExecutionJournalRecord.account_id == account_id,
                BasketExecutionJournalRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            if row is None:
                raise LookupError("durable basket submission claim not found")
            if row.state != "CLAIMED":
                raise ValueError("submission can start only from a fresh durable claim")
            row.state = "SUBMITTING"
            if set(expected_quantities) != set(client_order_ids):
                raise ValueError("expected quantities must match the durable basket leg IDs")
            row.client_order_ids_json = json.dumps(list(client_order_ids))
            self._append(db, row, "SUBMISSION_STARTED", {
                "client_order_ids": list(client_order_ids),
                "expected_quantities": expected_quantities,
            })

    async def record_order_ack(self, *, account_id: str, intent_id: str,
                               ack: BrokerOrderAck) -> None:
        await self._call(self._record_order_ack, account_id=account_id,
                         intent_id=intent_id, ack=ack)

    def _record_order_ack(self, *, account_id: str, intent_id: str,
                          ack: BrokerOrderAck) -> None:
        with self._session_factory() as db, db.begin():
            row = db.execute(select(BasketExecutionJournalRecord).where(
                BasketExecutionJournalRecord.account_id == account_id,
                BasketExecutionJournalRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            if row is None:
                raise LookupError("durable basket submission claim not found")
            expected = json.loads(row.client_order_ids_json)
            if ack.client_order_id not in expected:
                raise ValueError("broker acknowledgement client ID is not part of this basket")
            self._append(db, row, "BROKER_ORDER_ACK", {
                "client_order_id": ack.client_order_id,
                "broker_order_id": ack.broker_order_id,
                "status": ack.status,
                "accepted": ack.accepted,
            })

    async def freeze_intent(self, *, account_id: str, intent_id: str, reason: str) -> None:
        await self._call(self._mutate, account_id=account_id, intent_id=intent_id,
                         event_type="INTENT_FROZEN", payload={"reason": reason},
                         state="FROZEN", reason=reason)

    async def complete_filled_basket(self, *, account_id: str, intent_id: str,
                                     reconciliation: BasketReconciliation) -> None:
        await self._call(self._complete_filled_basket, account_id=account_id,
                         intent_id=intent_id, reconciliation=reconciliation)

    def _complete_filled_basket(self, *, account_id: str, intent_id: str,
                                reconciliation: BasketReconciliation) -> None:
        if not (
            reconciliation.authoritative and reconciliation.orders_complete
            and reconciliation.trades_complete and reconciliation.positions_reconciled
        ):
            raise ValueError("basket cannot complete without authoritative complete reconciliation")
        with self._session_factory() as db, db.begin():
            row = db.execute(select(BasketExecutionJournalRecord).where(
                BasketExecutionJournalRecord.account_id == account_id,
                BasketExecutionJournalRecord.intent_id == intent_id,
            ).with_for_update()).scalar_one_or_none()
            if row is None:
                raise LookupError("durable basket submission claim not found")
            if row.state != "SUBMITTING":
                raise ValueError("only a submitting basket can complete as filled")
            expected = json.loads(row.client_order_ids_json)
            start_event = db.execute(select(BasketExecutionJournalEventRecord).where(
                BasketExecutionJournalEventRecord.journal_id == row.id,
                BasketExecutionJournalEventRecord.event_type == "SUBMISSION_STARTED",
            ).order_by(BasketExecutionJournalEventRecord.sequence.desc()).limit(1)).scalar_one_or_none()
            expected_quantities = (
                json.loads(start_event.payload_json).get("expected_quantities", {})
                if start_event is not None else {}
            )
            by_id = {leg.client_order_id: leg for leg in reconciliation.legs}
            if (
                not expected or set(expected_quantities) != set(expected)
                or len(by_id) != len(expected) or set(by_id) != set(expected)
                or any(
                    not by_id[key].broker_order_id
                    or not by_id[key].order_terminal
                    or by_id[key].filled_quantity != expected_quantities[key]
                    for key in expected
                )
            ):
                raise ValueError("reconciliation does not prove every basket leg filled its exact target quantity")
            row.state = "FILLED"
            row.reconciliation_json = json.dumps({
                "authoritative": reconciliation.authoritative,
                "orders_complete": reconciliation.orders_complete,
                "trades_complete": reconciliation.trades_complete,
                "positions_reconciled": reconciliation.positions_reconciled,
                "legs": [asdict(leg) for leg in reconciliation.legs],
            }, sort_keys=True, separators=(",", ":"))
            self._append(db, row, "BASKET_FILLED_RECONCILED", {
                "client_order_ids": expected,
            })

    async def reject_preflight(self, *, account_id: str, intent_id: str, reason: str) -> None:
        await self._call(self._record_preflight_rejection, account_id=account_id,
                         intent_id=intent_id, reason=reason)

    def _record_preflight_rejection(self, *, account_id: str, intent_id: str,
                                    reason: str) -> None:
        now = time.time_ns()
        with self._session_factory() as db:
            try:
                with db.begin():
                    row = BasketExecutionJournalRecord(
                        account_id=account_id, intent_id=intent_id,
                        strategy="UNKNOWN", snapshot_id="NONE",
                        state="PREFLIGHT_REJECTED", client_order_ids_json="[]",
                        reason=reason, version=0, created_at_ns=now, updated_at_ns=now,
                    )
                    db.add(row)
                    db.flush()
                    self._append(db, row, "PREFLIGHT_REJECTED", {"reason": reason})
                return
            except IntegrityError:
                # A prior submission claim wins. Do not overwrite its history.
                return

    async def require_manual_review(self, *, account_id: str, intent_id: str,
                                    reason: str) -> None:
        await self._call(self._mutate, account_id=account_id, intent_id=intent_id,
                         event_type="MANUAL_REVIEW_REQUIRED",
                         payload={"reason": reason}, state="MANUAL_REVIEW", reason=reason)
