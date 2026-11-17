"""Read-only, operator-requested deferred-fill replay eligibility review.

This module NEVER replays callbacks or writes execution state. It only checks
whether an explicitly selected set of persisted callback events is internally
consistent enough to be considered for a separately implemented operator
workflow. Eligibility is not authorization to execute.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import math
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.broker_order_event import BrokerOrderEventRecord
from app.models.execution_intent import (
    ExecutionBrokerOrderRecord,
    ExecutionFillRecord,
    ExecutionIntentRecord,
    ExecutionLegRecord,
)


CONFIRMATION = "REVIEW DEFERRED FILL REPLAY ELIGIBILITY"


def review_operator_replay_eligibility(
    db: Session, *, broker: str, account_id: str, event_ids: tuple[str, ...],
    confirmation: str,
) -> dict:
    """Review an explicitly selected event set; never apply or replay it."""
    broker_key = str(broker or "").strip().lower()
    account_key = str(account_id or "").strip()
    selected = tuple(dict.fromkeys(str(value or "").strip() for value in event_ids))
    if not broker_key or not account_key:
        raise ValueError("broker and account_id are required")
    if not selected or len(selected) > 100 or any(not value for value in selected):
        raise ValueError("between 1 and 100 non-empty event_ids are required")
    if len(selected) != len(event_ids):
        raise ValueError("event_ids must be unique")
    if str(confirmation or "").strip() != CONFIRMATION:
        raise ValueError("explicit replay-eligibility confirmation is required")

    rows = db.execute(select(BrokerOrderEventRecord).where(
        BrokerOrderEventRecord.broker == broker_key,
        BrokerOrderEventRecord.account_id == account_key,
        BrokerOrderEventRecord.event_id.in_(selected),
    )).scalars().all()
    by_event_id = {row.event_id: row for row in rows}
    missing = [event_id for event_id in selected if event_id not in by_event_id]
    if missing:
        return {
            "status": "BLOCKED", "reason": "EVENT_NOT_FOUND_IN_BOUND_ACCOUNT",
            "requested_event_count": len(selected), "eligible": False,
            "events": [{"event_id": value, "reason": "EVENT_NOT_FOUND_IN_BOUND_ACCOUNT"} for value in missing],
            "trading_authorized": False, "replay_performed": False,
        }

    events = [by_event_id[value] for value in selected]
    fill_ids = [event.fill_id for event in events if event.fill_id]
    existing_fills = {}
    if fill_ids:
        found = db.execute(select(ExecutionFillRecord).where(
            ExecutionFillRecord.account_id == account_key,
            ExecutionFillRecord.broker == broker_key,
            ExecutionFillRecord.broker_fill_id.in_(fill_ids),
        )).scalars().all()
        existing_fills = {row.broker_fill_id: row for row in found}

    order_ids = {event.broker_order_id for event in events}
    orders = db.execute(select(ExecutionBrokerOrderRecord).where(
        ExecutionBrokerOrderRecord.account_id == account_key,
        ExecutionBrokerOrderRecord.broker == broker_key,
        ExecutionBrokerOrderRecord.broker_order_id.in_(order_ids),
    )).scalars().all()
    order_by_id = {row.broker_order_id: row for row in orders}

    issues_by_event: dict[str, list[str]] = defaultdict(list)
    intent_groups: dict[int, list[tuple[BrokerOrderEventRecord, ExecutionBrokerOrderRecord]]] = defaultdict(list)
    for event in events:
        if event.status not in {"PARTIALLY_FILLED", "FILLED"}:
            issues_by_event[event.event_id].append("EVENT_STATUS_NOT_FILL_BEARING")
        if (
            not event.fill_id or not event.fill_quantity
            or event.average_fill_price is None
            or not math.isfinite(float(event.average_fill_price))
            or float(event.average_fill_price) <= 0
        ):
            issues_by_event[event.event_id].append("FILL_IDENTITY_OR_EXECUTION_EVIDENCE_MISSING")
        if event.fill_quantity is not None and event.fill_quantity <= 0:
            issues_by_event[event.event_id].append("INVALID_FILL_QUANTITY")
        if event.occurred_at_ns <= 0:
            issues_by_event[event.event_id].append("FILL_TIMESTAMP_INVALID")
        if event.fill_id and event.fill_id in existing_fills:
            issues_by_event[event.event_id].append("FILL_ALREADY_PRESENT_IN_IMMUTABLE_LEDGER")
        order = order_by_id.get(event.broker_order_id)
        if order is None:
            issues_by_event[event.event_id].append("BROKER_ORDER_IDENTITY_UNRESOLVED")
            continue
        if event.fill_quantity and event.fill_quantity > order.quantity:
            issues_by_event[event.event_id].append("FILL_EXCEEDS_REGISTERED_ORDER_QUANTITY")
        if event.cumulative_filled_quantity > order.quantity:
            issues_by_event[event.event_id].append("CUMULATIVE_QUANTITY_EXCEEDS_REGISTERED_ORDER")
        intent_groups[order.intent_record_id].append((event, order))

    # Build projected per-leg quantities for the complete explicitly selected
    # event set. This is a calculation only; no fill rows or intent fields change.
    for intent_record_id, pairs in intent_groups.items():
        intent = db.get(ExecutionIntentRecord, intent_record_id)
        if intent is None or intent.account_id != account_key:
            for event, _ in pairs:
                issues_by_event[event.event_id].append("EXECUTION_INTENT_SCOPE_UNRESOLVED")
            continue
        legs = db.execute(select(ExecutionLegRecord).where(
            ExecutionLegRecord.intent_record_id == intent_record_id,
        )).scalars().all()
        if len(legs) < 2:
            for event, _ in pairs:
                issues_by_event[event.event_id].append("MULTI_LEG_INTENT_NOT_CONFIRMED")
            continue
        leg_by_id = {leg.id: leg for leg in legs}
        orders_for_intent = db.execute(select(ExecutionBrokerOrderRecord).where(
            ExecutionBrokerOrderRecord.intent_record_id == intent_record_id,
            ExecutionBrokerOrderRecord.account_id == account_key,
            ExecutionBrokerOrderRecord.broker == broker_key,
        )).scalars().all()
        # An explicit subset must not hide another unresolved fill-bearing
        # callback for the same intent. Exact duplicate callback evidence, or
        # evidence already represented exactly in the immutable fill ledger,
        # is not unresolved and therefore does not block the selected set.
        related_order_ids = [order.broker_order_id for order in orders_for_intent]
        related_events = []
        if related_order_ids:
            related_events = db.execute(select(BrokerOrderEventRecord).where(
                BrokerOrderEventRecord.broker == broker_key,
                BrokerOrderEventRecord.account_id == account_key,
                BrokerOrderEventRecord.broker_order_id.in_(related_order_ids),
                BrokerOrderEventRecord.status.in_(("PARTIALLY_FILLED", "FILLED")),
            )).scalars().all()
        related_fill_ids = {row.fill_id for row in related_events if row.fill_id}
        related_fills = {}
        if related_fill_ids:
            related_fills = {
                row.broker_fill_id: row
                for row in db.execute(select(ExecutionFillRecord).where(
                    ExecutionFillRecord.account_id == account_key,
                    ExecutionFillRecord.broker == broker_key,
                    ExecutionFillRecord.broker_fill_id.in_(related_fill_ids),
                )).scalars().all()
            }
        selected_by_fill_id = {event.fill_id: event for event, _ in pairs if event.fill_id}
        unresolved_outside_selection = False
        for related in related_events:
            if related.event_id in selected:
                continue
            selected_duplicate = selected_by_fill_id.get(related.fill_id) if related.fill_id else None
            if selected_duplicate is not None:
                same_selected_evidence = (
                    related.broker_order_id == selected_duplicate.broker_order_id
                    and related.fill_quantity == selected_duplicate.fill_quantity
                    and related.occurred_at_ns == selected_duplicate.occurred_at_ns
                    and related.average_fill_price is not None
                    and selected_duplicate.average_fill_price is not None
                    and Decimal(str(related.average_fill_price))
                    == Decimal(str(selected_duplicate.average_fill_price))
                )
                if same_selected_evidence:
                    continue
            ledger_fill = related_fills.get(related.fill_id) if related.fill_id else None
            if ledger_fill is not None:
                ledger_evidence_matches = (
                    ledger_fill.broker_order_id == related.broker_order_id
                    and ledger_fill.quantity == related.fill_quantity
                    and related.average_fill_price is not None
                    and Decimal(str(ledger_fill.fill_price))
                    == Decimal(str(related.average_fill_price))
                    and ledger_fill.occurred_at_ns == related.occurred_at_ns
                )
                if ledger_evidence_matches:
                    continue
            unresolved_outside_selection = True
        if unresolved_outside_selection:
            for event, _ in pairs:
                issues_by_event[event.event_id].append(
                    "UNSELECTED_UNRESOLVED_FILL_EVENT_IN_INTENT"
                )

        orders_by_leg: dict[int, list[ExecutionBrokerOrderRecord]] = defaultdict(list)
        for registered_order in orders_for_intent:
            orders_by_leg[registered_order.leg_record_id].append(registered_order)
        order_by_leg = {leg_id: values[0] for leg_id, values in orders_by_leg.items() if len(values) == 1}
        current_totals = db.execute(select(
            ExecutionFillRecord.leg_record_id,
            func.coalesce(func.sum(ExecutionFillRecord.quantity), 0),
        ).where(
            ExecutionFillRecord.intent_record_id == intent_record_id,
            ExecutionFillRecord.account_id == account_key,
            ExecutionFillRecord.broker == broker_key,
        ).group_by(ExecutionFillRecord.leg_record_id)).all()
        projected = {int(leg_id): int(leg_total) for leg_id, leg_total in current_totals}
        for leg in legs:
            projected.setdefault(leg.id, 0)

        by_order: dict[str, list[BrokerOrderEventRecord]] = defaultdict(list)
        for event, order in pairs:
            by_order[order.broker_order_id].append(event)
            projected[order.leg_record_id] += int(event.fill_quantity or 0)

        for order_id, order_events in by_order.items():
            order = order_by_id[order_id]
            running = db.execute(select(
                func.coalesce(func.sum(ExecutionFillRecord.quantity), 0)
            ).where(
                ExecutionFillRecord.account_id == account_key,
                ExecutionFillRecord.broker == broker_key,
                ExecutionFillRecord.broker_order_id == order_id,
            )).scalar_one()
            for event in sorted(order_events, key=lambda item: (item.cumulative_filled_quantity, item.occurred_at_ns, item.id)):
                running += int(event.fill_quantity or 0)
                if running != event.cumulative_filled_quantity:
                    issues_by_event[event.event_id].append("CUMULATIVE_FILL_SEQUENCE_NOT_CONTIGUOUS")
                if event.status == "FILLED" and event.cumulative_filled_quantity != order.quantity:
                    issues_by_event[event.event_id].append("FILLED_STATUS_QUANTITY_CONFLICT")

        # Every leg must have an exact persisted broker-order identity, and
        # projected fill quantities must balance across all legs.
        missing_order_legs = [leg for leg in legs if leg.id not in order_by_leg]
        if missing_order_legs or any(len(values) != 1 for values in orders_by_leg.values()):
            for event, _ in pairs:
                issues_by_event[event.event_id].append("MULTI_LEG_BROKER_ORDER_IDENTITY_INCOMPLETE")
        for leg in legs:
            registered = order_by_leg.get(leg.id)
            if registered is not None and (
                registered.symbol != leg.instrument
                or registered.side != leg.side
                or registered.quantity != leg.quantity
            ):
                for event, _ in pairs:
                    issues_by_event[event.event_id].append("BROKER_ORDER_LEG_IDENTITY_MISMATCH")
        for event, order in pairs:
            leg = leg_by_id.get(order.leg_record_id)
            if leg is None or order.symbol != leg.instrument or order.side != leg.side or order.quantity != leg.quantity:
                issues_by_event[event.event_id].append("BROKER_ORDER_LEG_IDENTITY_MISMATCH")
        if len(set(projected.values())) != 1:
            quantities = ",".join(f"{leg_by_id[key].leg_id}:{value}" for key, value in sorted(projected.items()))
            for event, _ in pairs:
                issues_by_event[event.event_id].append("MULTI_LEG_PROJECTED_FILL_QUANTITY_MISMATCH:" + quantities)

    results = []
    for event in events:
        reasons = sorted(set(issues_by_event[event.event_id]))
        results.append({
            "event_id": event.event_id,
            "broker_order_id": event.broker_order_id,
            "fill_id": event.fill_id,
            "eligible": not reasons,
            "reasons": reasons,
        })
    eligible = bool(results) and all(item["eligible"] for item in results)
    return {
        "status": "ELIGIBLE_FOR_SEPARATE_OPERATOR_REVIEW" if eligible else "BLOCKED",
        "reason": None if eligible else "ONE_OR_MORE_REPLAY_ELIGIBILITY_GUARDS_FAILED",
        "broker": broker_key,
        "account_id": account_key,
        "requested_event_count": len(selected),
        "eligible": eligible,
        "events": results,
        "replay_performed": False,
        "fill_ledger_mutated": False,
        "intent_state_mutated": False,
        "trading_authorized": False,
    }
