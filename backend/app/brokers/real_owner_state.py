"""Durable Box Spread real-owner account binding.

Only a keyed fingerprint of the authenticated broker client code is stored.
Broker passwords, TOTP secrets, API keys, and session tokens are never persisted.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException
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
    fingerprint = account_fingerprint(broker_name, client_code)
    state = (
        db.query(BoxSpreadRealOwnerState)
        .filter(BoxSpreadRealOwnerState.owner_key == OWNER_KEY)
        .with_for_update()
        .first()
    )
    if state is None:
        state = BoxSpreadRealOwnerState(
            owner_key=OWNER_KEY,
            broker_name=broker_name.strip().lower(),
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
        state.broker_name = broker_name.strip().lower()
        # Account binding is durable; live session state is intentionally not.
        state.connected = False
        state.real_trading_enabled = False
        state.kill_switch = True
    try:
        db.commit()
        db.refresh(state)
    except Exception:
        db.rollback()
        raise
    return state
