"""Durable Box Spread real-owner account binding."""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.box_spread_real_owner_state import BoxSpreadRealOwnerState

OWNER_KEY = "box-spread-real-owner"


def account_fingerprint(broker_name: str, client_code: str) -> str:
    """Return a stable, non-reversible keyed fingerprint for an account identity."""
    key = settings.SECRET_KEY.encode("utf-8")
    if len(key) < 32:
        raise HTTPException(
            status_code=503,
            detail="A strong server SECRET_KEY is required for durable account ownership",
        )
    identity = f"{broker_name.strip().lower()}:{client_code.strip()}".encode("utf-8")
    return hmac.new(key, identity, hashlib.sha256).hexdigest()


def claim_authenticated_account(db: Session, broker_name: str, client_code: str) -> BoxSpreadRealOwnerState:
    """Bind the dedicated owner to one broker account, failing closed on changes."""
    normalized_broker = (broker_name or "").strip().lower()
    normalized_client = (client_code or "").strip()
    if not normalized_broker or not normalized_client:
        raise HTTPException(status_code=503, detail="Authenticated broker account identity is required")

    fingerprint = account_fingerprint(normalized_broker, normalized_client)
    state = (
        db.query(BoxSpreadRealOwnerState)
        .filter(BoxSpreadRealOwnerState.owner_key == OWNER_KEY)
        .with_for_update()
        .first()
    )
    if state is None:
        state = BoxSpreadRealOwnerState(
            owner_key=OWNER_KEY,
            broker_name=normalized_broker,
            account_fingerprint=fingerprint,
            connected=False,
            real_trading_enabled=False,
            kill_switch=True,
        )
        db.add(state)
    elif not hmac.compare_digest(state.account_fingerprint or "", fingerprint):
        raise HTTPException(
            status_code=409,
            detail="A different broker account is already bound to the Box Spread real owner; manual ownership recovery is required",
        )
    else:
        state.broker_name = normalized_broker
        state.connected = False
        state.real_trading_enabled = False
        state.kill_switch = True
    try:
        db.commit()
        db.refresh(state)
    except IntegrityError as exc:
        # A first-time concurrent claim may not lock a row that does not yet
        # exist. The unique owner_key constraint is the final arbiter; after a
        # collision, re-read the committed owner and never overwrite it.
        db.rollback()
        persisted = (
            db.query(BoxSpreadRealOwnerState)
            .filter(BoxSpreadRealOwnerState.owner_key == OWNER_KEY)
            .first()
        )
        if persisted is not None and not hmac.compare_digest(
            persisted.account_fingerprint or "", fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail="A different broker account won the Box Spread ownership claim; manual ownership recovery is required",
            ) from exc
        raise HTTPException(
            status_code=503,
            detail="Concurrent Box Spread account ownership claim could not be safely resolved; retry after checking the persisted owner",
        ) from exc
    except Exception:
        db.rollback()
        raise
    return state



def set_owner_connection_state(db: Session, connected: bool) -> BoxSpreadRealOwnerState:
    """Persist broker session state while keeping trading disarmed."""
    state = (
        db.query(BoxSpreadRealOwnerState)
        .filter(BoxSpreadRealOwnerState.owner_key == OWNER_KEY)
        .with_for_update()
        .first()
    )
    if state is None or not state.account_fingerprint:
        raise HTTPException(status_code=503, detail="Durable Box Spread account ownership is unavailable")
    state.connected = bool(connected)
    state.real_trading_enabled = False
    state.kill_switch = True
    try:
        db.commit()
        db.refresh(state)
    except Exception:
        db.rollback()
        raise
    return state



def reconcile_owner_state_on_startup(db: Session) -> bool:
    """Clear stale process-local connection claims after a service restart.

    A restarted process has no restored broker adapter session, so persisted
    connected=True is never trusted. Any existing owner is returned to the
    fail-closed state; no row is created when ownership has not been claimed.
    """
    state = (
        db.query(BoxSpreadRealOwnerState)
        .filter(BoxSpreadRealOwnerState.owner_key == OWNER_KEY)
        .with_for_update()
        .first()
    )
    if state is None:
        return False
    state.connected = False
    state.real_trading_enabled = False
    state.kill_switch = True
    try:
        db.commit()
        db.refresh(state)
    except Exception:
        db.rollback()
        raise
    return True
