"""Read-only orchestration of durable recovery evidence and broker history.

The adapter must already represent the authenticated single-account session.
This flow reads today's broker books and the local ledger, then returns a
review-only disposition. It never resumes, retries, places, or cancels orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.execution.recovery import RecoveryCandidate
from app.execution.recovery_ledger import load_recovery_ledger_evidence
from app.execution.recovery_review import (
    RecoveryDisposition,
    RecoveryReview,
    review_recovery_candidate,
)


@dataclass(frozen=True)
class RecoverySnapshotResult:
    """Read-only outcome; blocked outcomes never imply a safe retry."""

    disposition: RecoveryDisposition
    review: RecoveryReview | None
    reason: str | None
    trading_date: str | None

    @property
    def is_consistent_read_only(self) -> bool:
        return (
            self.disposition is RecoveryDisposition.CONSISTENT_READ_ONLY
            and self.review is not None
            and self.review.is_consistent
        )


def review_with_broker_history(
    db: Any,
    *,
    candidate: RecoveryCandidate,
    broker: str,
    adapter: Any,
) -> RecoverySnapshotResult:
    """Fetch a complete current-day broker snapshot and review one intent.

    Adapter history methods are read-only and must enforce their own
    authenticated account scope. Any adapter error, unsupported history scope,
    incomplete book, or local ledger mismatch fails closed. The returned
    consistency state is evidence only and is never execution authorization.
    """
    broker_key = str(broker or "").strip().lower()
    if not broker_key:
        raise ValueError("broker is required")
    account_id = candidate.persisted.intent.account_id
    if not account_id or not account_id.strip():
        raise ValueError("recovery candidate account identity is required")

    fetch = getattr(adapter, "fetch_today_broker_snapshot", None)
    if not callable(fetch):
        return RecoverySnapshotResult(
            RecoveryDisposition.BLOCKED, None,
            "BROKER_HISTORY_UNAVAILABLE", None,
        )

    try:
        history = fetch(account_id=account_id)
    except Exception:
        # Do not leak credentials/provider payloads into recovery status.
        return RecoverySnapshotResult(
            RecoveryDisposition.BLOCKED, None,
            "BROKER_HISTORY_FETCH_FAILED", None,
        )

    if getattr(history, "scope", None) != "CURRENT_TRADING_DAY":
        return RecoverySnapshotResult(
            RecoveryDisposition.BLOCKED, None,
            "BROKER_HISTORY_SCOPE_UNSUPPORTED", getattr(history, "trading_date", None),
        )
    snapshot = getattr(history, "snapshot", None)
    if snapshot is None:
        return RecoverySnapshotResult(
            RecoveryDisposition.BLOCKED, None,
            "BROKER_HISTORY_SNAPSHOT_MISSING", getattr(history, "trading_date", None),
        )

    try:
        evidence = load_recovery_ledger_evidence(
            db, candidate=candidate, broker=broker_key,
        )
        review = review_recovery_candidate(
            candidate=candidate,
            broker=broker_key,
            snapshot=snapshot,
            local_orders=evidence.local_orders,
            local_fill_quantities=evidence.local_fill_quantities,
        )
    except (ValueError, LookupError):
        return RecoverySnapshotResult(
            RecoveryDisposition.BLOCKED, None,
            "LOCAL_LEDGER_OR_SNAPSHOT_SCOPE_INVALID",
            getattr(history, "trading_date", None),
        )

    return RecoverySnapshotResult(
        disposition=review.disposition,
        review=review,
        reason=None if review.is_consistent else "RECONCILIATION_REQUIRES_REVIEW",
        trading_date=getattr(history, "trading_date", None),
    )
