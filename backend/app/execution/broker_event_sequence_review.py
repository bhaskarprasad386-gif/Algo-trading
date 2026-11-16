"""Read-only callback arrival/sequence diagnostics for broker fill events."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Any


def review_callback_sequence(events: Iterable[Any]) -> dict:
    """Detect persisted callback ordering anomalies without applying any fill.

    Database row IDs represent local arrival order. Broker occurrence timestamps
    and cumulative quantities provide independent ordering evidence. Events with
    missing timestamps or fill identity are excluded from ordering conclusions.
    """
    by_order: dict[str, list[Any]] = defaultdict(list)
    for event in events:
        if (
            getattr(event, "fill_id", None)
            and int(getattr(event, "occurred_at_ns", 0) or 0) > 0
            and int(getattr(event, "cumulative_filled_quantity", 0) or 0) > 0
        ):
            by_order[str(event.broker_order_id)].append(event)

    issues: list[dict[str, str]] = []
    for order_id, rows in sorted(by_order.items()):
        arrival_order = sorted(rows, key=lambda row: (int(row.id), str(row.event_id)))
        seen_fill_ids: dict[str, Any] = {}
        previous_time = 0
        previous_cumulative = 0
        for row in arrival_order:
            fill_id = str(row.fill_id)
            if fill_id in seen_fill_ids:
                issues.append({
                    "broker_order_id": order_id,
                    "event_id": str(row.event_id),
                    "reason": "DUPLICATE_FILL_IDENTITY",
                    "related_event_id": str(seen_fill_ids[fill_id].event_id),
                })
            else:
                seen_fill_ids[fill_id] = row

            occurred = int(row.occurred_at_ns)
            cumulative = int(row.cumulative_filled_quantity)
            if previous_time and occurred < previous_time:
                issues.append({
                    "broker_order_id": order_id,
                    "event_id": str(row.event_id),
                    "reason": "CALLBACK_ARRIVED_OUT_OF_ORDER",
                    "related_event_id": "",
                })
            if previous_cumulative and cumulative <= previous_cumulative:
                issues.append({
                    "broker_order_id": order_id,
                    "event_id": str(row.event_id),
                    "reason": "CUMULATIVE_SEQUENCE_NOT_INCREASING",
                    "related_event_id": "",
                })
            previous_time = max(previous_time, occurred)
            previous_cumulative = max(previous_cumulative, cumulative)

    reason_counts: dict[str, int] = {}
    for issue in issues:
        reason_counts[issue["reason"]] = reason_counts.get(issue["reason"], 0) + 1
    return {
        "status": "REQUIRES_REVIEW" if issues else "CLEAR",
        "examined_order_count": len(by_order),
        "examined_event_count": sum(len(rows) for rows in by_order.values()),
        "issue_count": len(issues),
        "reason_counts": reason_counts,
        "issues": issues,
        "trading_authorized": False,
        "fills_mutated": False,
    }
