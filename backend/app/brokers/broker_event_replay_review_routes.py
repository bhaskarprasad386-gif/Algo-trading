"""Owner-authenticated operator-requested replay eligibility review routes.

The endpoint is a dry-run review only. It never invokes a broker adapter,
replays callbacks, applies fills, or changes execution state.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.real_owner_auth import require_real_owner_access
from app.brokers.real_owner_state import matches_authenticated_account
from app.brokers.session_store import broker_sessions
from app.core.database import get_db
from app.core.logger import app_logger
from app.execution.broker_event_replay_review import (
    CONFIRMATION,
    review_operator_replay_eligibility,
)
from app.models import BoxSpreadRealOwnerState
from app.models.broker_replay_eligibility_audit import BrokerReplayEligibilityAuditRecord

router = APIRouter(tags=["brokers"])


class ReplayEligibilityRequest(BaseModel):
    event_ids: list[str] = Field(min_length=1, max_length=100)
    confirmation: str = Field(min_length=1, max_length=100)


@router.post("/events/replay-eligibility")
def review_requested_replay_eligibility(
    payload: ReplayEligibilityRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Explicitly review selected callback IDs; this endpoint does not replay."""
    owner_state = db.query(BoxSpreadRealOwnerState).filter(
        BoxSpreadRealOwnerState.owner_key == "box-spread-real-owner"
    ).first()
    if owner_state is None or not owner_state.broker_name:
        raise HTTPException(status_code=409, detail="No authenticated Box Spread broker account is bound")

    broker = str(owner_state.broker_name).strip().lower()
    with broker_sessions.lease(user_id, broker) as adapter:
        db.rollback()
        if adapter is None or not bool(getattr(adapter, "connected", False)):
            return {
                "status": "BLOCKED", "reason": "BROKER_SESSION_NOT_CONNECTED",
                "eligible": False, "events": [], "replay_performed": False,
                "trading_authorized": False,
            }
        if str(getattr(adapter, "name", "") or "").strip().lower() != broker:
            return {
                "status": "BLOCKED", "reason": "BROKER_SESSION_IDENTITY_MISMATCH",
                "eligible": False, "events": [], "replay_performed": False,
                "trading_authorized": False,
            }
        account_id = str(getattr(adapter, "account_id", "") or "").strip()
        if not account_id:
            return {
                "status": "BLOCKED", "reason": "BROKER_SESSION_ACCOUNT_ID_UNAVAILABLE",
                "eligible": False, "events": [], "replay_performed": False,
                "trading_authorized": False,
            }
        if not matches_authenticated_account(owner_state, broker, account_id):
            raise HTTPException(
                status_code=409,
                detail="Active broker session does not match the durably bound Box Spread account",
            )
        try:
            result = review_operator_replay_eligibility(
                db, broker=broker, account_id=account_id,
                event_ids=tuple(payload.event_ids),
                confirmation=payload.confirmation,
            )
            reviewed_at_ns = time.time_ns()
            valid_until_ns = reviewed_at_ns + 60 * 1_000_000_000
            canonical_events = json.dumps(sorted(payload.event_ids), separators=(",", ":"))
            digest_material = json.dumps({
                "broker": broker, "account_id": account_id,
                "event_ids": sorted(payload.event_ids),
                "result": result,
            }, sort_keys=True, separators=(",", ":"), default=str)
            review_id = uuid.uuid4().hex
            audit = BrokerReplayEligibilityAuditRecord(
                review_id=review_id, broker=broker, account_id=account_id,
                event_ids_json=canonical_events,
                evidence_digest=hashlib.sha256(digest_material.encode("utf-8")).hexdigest(),
                result_status=str(result.get("status", "BLOCKED")),
                eligible=bool(result.get("eligible", False)),
                reviewed_at_ns=reviewed_at_ns, valid_until_ns=valid_until_ns,
            )
            db.add(audit)
            db.commit()
            result["review_audit"] = {
                "review_id": review_id,
                "evidence_digest": audit.evidence_digest,
                "reviewed_at_ns": reviewed_at_ns,
                "valid_until_ns": valid_until_ns,
                "validity_seconds": 60,
                "approval_granted": False,
                "replay_authorized": False,
            }
            return result
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail="Replay eligibility review request is invalid") from exc
        except Exception as exc:
            db.rollback()
            app_logger.exception("Operator replay eligibility review failed")
            raise HTTPException(status_code=503, detail="Replay eligibility review is temporarily unavailable") from exc


@router.get("/events/replay-eligibility/audit/{review_id}")
def get_replay_eligibility_audit(
    review_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Read one audit receipt in the currently bound single broker account."""
    owner_state = db.query(BoxSpreadRealOwnerState).filter(
        BoxSpreadRealOwnerState.owner_key == "box-spread-real-owner"
    ).first()
    if owner_state is None or not owner_state.broker_name:
        raise HTTPException(status_code=409, detail="No authenticated Box Spread broker account is bound")
    broker = str(owner_state.broker_name).strip().lower()
    with broker_sessions.lease(user_id, broker) as adapter:
        if adapter is None or not bool(getattr(adapter, "connected", False)):
            raise HTTPException(status_code=409, detail="Bound broker session is not connected")
        if str(getattr(adapter, "name", "") or "").strip().lower() != broker:
            raise HTTPException(status_code=409, detail="Bound broker session identity does not match owner binding")
        account_id = str(getattr(adapter, "account_id", "") or "").strip()
        if not account_id:
            raise HTTPException(status_code=409, detail="Bound broker account identity is unavailable")
        if not matches_authenticated_account(owner_state, broker, account_id):
            raise HTTPException(
                status_code=409,
                detail="Active broker session does not match the durably bound Box Spread account",
            )
        audit = db.query(BrokerReplayEligibilityAuditRecord).filter(
            BrokerReplayEligibilityAuditRecord.review_id == review_id,
            BrokerReplayEligibilityAuditRecord.broker == broker,
            BrokerReplayEligibilityAuditRecord.account_id == account_id,
        ).first()
        if audit is None:
            raise HTTPException(status_code=404, detail="Replay eligibility audit record not found")
        now_ns = time.time_ns()
        expired = now_ns >= audit.valid_until_ns
        return {
            "review_id": audit.review_id,
            "broker": audit.broker,
            "reviewed_at_ns": audit.reviewed_at_ns,
            "valid_until_ns": audit.valid_until_ns,
            "expired": expired,
            "status": "EXPIRED" if expired else "CURRENT",
            "result_status": audit.result_status,
            "eligible_at_review": bool(audit.eligible),
            "evidence_digest": audit.evidence_digest,
            "approval_granted": False,
            "replay_authorized": False,
            "trading_authorized": False,
        }


    __all__ = ["CONFIRMATION", "router"]
