"""Owner-authenticated operator-requested replay eligibility review routes.

The endpoint is a dry-run review only. It never invokes a broker adapter,
replays callbacks, applies fills, or changes execution state.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.real_owner_auth import require_real_owner_access
from app.brokers.session_store import broker_sessions
from app.core.database import get_db
from app.core.logger import app_logger
from app.execution.broker_event_replay_review import (
    CONFIRMATION,
    review_operator_replay_eligibility,
)
from app.models import BoxSpreadRealOwnerState

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
    adapter = broker_sessions.get(user_id, broker)
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
    try:
        return review_operator_replay_eligibility(
            db, broker=broker, account_id=account_id,
            event_ids=tuple(payload.event_ids),
            confirmation=payload.confirmation,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail="Replay eligibility review request is invalid") from exc
    except Exception as exc:
        db.rollback()
        app_logger.exception("Operator replay eligibility review failed")
        raise HTTPException(status_code=503, detail="Replay eligibility review is temporarily unavailable") from exc


__all__ = ["CONFIRMATION", "router"]
