"""Account-scoped, read-only orchestration for Batch 0 restart recovery.

Fetches one complete broker order/trade snapshot per authenticated session and
reuses it to review every non-terminal intent. This module never submits,
cancels, adopts, or resumes orders and never changes execution state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.execution.recovery import list_recovery_candidates
from app.execution.recovery_ledger import load_recovery_ledger_evidence
from app.execution.recovery_review import review_recovery_candidate


def review_account_recovery(
    db: Any,
    *,
    account_id: str,
    broker: str,
    adapter: Any,
    limit: int = 500,
) -> dict[str, Any]:
    """Review all pending intents against one authenticated current-day snapshot.

    A single broker history request is shared across the account's candidates;
    this avoids N identical provider calls when many intents need review.
    Every unsupported, incomplete, or mismatched condition fails closed.
    """
    account = str(account_id or "").strip()
    broker_key = str(broker or "").strip().lower()
    if not account or not broker_key:
        raise ValueError("account_id and broker are required")
    if str(getattr(adapter, "name", "")).strip().lower() != broker_key:
        return {
            "status": "BLOCKED",
            "reason": "BROKER_SESSION_IDENTITY_MISMATCH",
            "trading_authorized": False,
            "candidate_count": 0,
            "reviews": [],
        }
    if not bool(getattr(adapter, "connected", False)):
        return {
            "status": "BLOCKED",
            "reason": "BROKER_SESSION_NOT_CONNECTED",
            "trading_authorized": False,
            "candidate_count": 0,
            "reviews": [],
        }

    if str(getattr(adapter, "account_id", "") or "").strip() != account:
        return {
            "status": "BLOCKED",
            "reason": "BROKER_SESSION_ACCOUNT_SCOPE_MISMATCH",
            "trading_authorized": False,
            "candidate_count": 0,
            "reviews": [],
        }

    candidates = list_recovery_candidates(db, account_id=account, limit=limit)
    if not candidates:
        return {
            "status": "NO_PENDING_INTENTS",
            "reason": None,
            "trading_authorized": False,
            "candidate_count": 0,
            "reviews": [],
        }

    fetch = getattr(adapter, "fetch_today_broker_snapshot", None)
    if not callable(fetch):
        return {
            "status": "BLOCKED",
            "reason": "BROKER_HISTORY_UNAVAILABLE",
            "trading_authorized": False,
            "candidate_count": len(candidates),
            "reviews": [],
        }
    try:
        history = fetch(account_id=account)
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "BROKER_HISTORY_FETCH_FAILED",
            "trading_authorized": False,
            "candidate_count": len(candidates),
            "reviews": [],
        }

    trading_date = getattr(history, "trading_date", None)
    snapshot = getattr(history, "snapshot", None)
    current_trading_date = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    if getattr(history, "scope", None) != "CURRENT_TRADING_DAY":
        reason = "BROKER_HISTORY_SCOPE_UNSUPPORTED"
    elif trading_date != current_trading_date:
        reason = "BROKER_HISTORY_TRADING_DATE_STALE"
    elif snapshot is None:
        reason = "BROKER_HISTORY_SNAPSHOT_MISSING"
    elif getattr(snapshot, "complete", None) is not True:
        reason = "BROKER_HISTORY_SNAPSHOT_INCOMPLETE"
    elif (
        getattr(snapshot, "account_id", None) != account
        or str(getattr(snapshot, "broker", "")).strip().lower() != broker_key
    ):
        reason = "BROKER_HISTORY_ACCOUNT_SCOPE_MISMATCH"
    else:
        reason = None
    if reason:
        return {
            "status": "BLOCKED",
            "reason": reason,
            "trading_date": trading_date,
            "trading_authorized": False,
            "candidate_count": len(candidates),
            "reviews": [],
        }

    reviews: list[dict[str, Any]] = []
    for candidate in candidates:
        intent = candidate.persisted.intent
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
            reviews.append({
                "intent_id": intent.intent_id,
                "disposition": review.disposition.value,
                "matched_order_count": review.matched_order_count,
                "issues": [
                    {
                        "status": issue.status.value,
                        "identity": issue.identity,
                        "detail": issue.detail,
                    }
                    for issue in review.issues
                ],
            })
        except Exception:
            # Provider/ledger exception details may contain account data or
            # response payloads. Preserve a review-only result without leaking
            # them or implying that the affected intent is safe to resume.
            reviews.append({
                "intent_id": intent.intent_id,
                "disposition": "BLOCKED",
                "matched_order_count": 0,
                "issues": [{
                    "status": "LOCAL_LEDGER_OR_SNAPSHOT_SCOPE_INVALID",
                    "identity": intent.intent_id,
                    "detail": "Persisted execution evidence could not be safely reconciled.",
                }],
            })

    consistent = sum(
        1 for item in reviews if item["disposition"] == "CONSISTENT_READ_ONLY"
    )
    return {
        "status": "REVIEWED" if consistent == len(reviews) else "REQUIRES_REVIEW",
        "reason": None if consistent == len(reviews) else "ONE_OR_MORE_INTENTS_BLOCKED",
        "trading_date": trading_date,
        "trading_authorized": False,
        "candidate_count": len(candidates),
        "consistent_read_only_count": consistent,
        "blocked_count": len(reviews) - consistent,
        "reviews": reviews,
    }
