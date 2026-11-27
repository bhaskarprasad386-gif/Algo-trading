"""Dedicated single-owner authentication for real-trading broker management.

This gate is deliberately separate from paper-trading credentials. It does not
enable order routing or claim durable broker-account ownership.
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from datetime import timedelta

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token

OWNER_SUBJECT = "real-trading-owner"
OWNER_SCOPE = "real-trading-owner"
# A dedicated single-owner namespace, not a database user identity.
OWNER_NAMESPACE_ID = 0
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_ATTEMPTS = 5
_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


class RealOwnerLogin(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _configured_password() -> str:
    # Legacy deployment variable is accepted as a migration fallback; read at request time so
    # deployment configuration is never cached in a module-level variable.
    return os.environ.get("REAL_TRADING_OWNER_PASSWORD", "") or os.environ.get("BOX_SPREAD_REAL_OWNER_PASSWORD", "")


def _consume_attempt(host: str) -> None:
    now = time.monotonic()
    with _attempts_lock:
        recent = [stamp for stamp in _attempts.get(host, []) if now - stamp < _LOGIN_WINDOW_SECONDS]
        if len(recent) >= _LOGIN_MAX_ATTEMPTS:
            _attempts[host] = recent
            retry_after = max(1, int(_LOGIN_WINDOW_SECONDS - (now - recent[0])) + 1)
            raise HTTPException(
                status_code=429,
                detail="Too many real-trading owner login attempts; try again later",
                headers={"Retry-After": str(retry_after)},
            )
        recent.append(now)
        _attempts[host] = recent
        if len(_attempts) > 4096:
            for key, stamps in list(_attempts.items()):
                if not stamps or now - stamps[-1] >= _LOGIN_WINDOW_SECONDS:
                    _attempts.pop(key, None)


def login_real_owner(payload: RealOwnerLogin, request: Request | None = None) -> dict:
    configured = _configured_password()
    if not configured:
        raise HTTPException(status_code=503, detail="real-trading real-owner authentication is not configured")
    if len(configured) < 16:
        raise HTTPException(status_code=503, detail="real-trading real-owner password must be at least 16 characters")
    if len(settings.SECRET_KEY) < 32:
        raise HTTPException(status_code=503, detail="A strong server SECRET_KEY is required for owner sessions")
    host = request.client.host if request and request.client else "unknown"
    _consume_attempt(host)
    if not hmac.compare_digest(payload.password.encode("utf-8"), configured.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid real-trading owner credentials")
    with _attempts_lock:
        _attempts.pop(host, None)
    token = create_access_token(
        {"sub": OWNER_SUBJECT, "scope": OWNER_SCOPE},
        expires_delta=timedelta(minutes=30),
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 1800}


def require_real_owner_access(authorization: str | None = Header(default=None)) -> int:
    configured = _configured_password()
    if not configured or len(configured) < 16 or len(settings.SECRET_KEY) < 32:
        raise HTTPException(status_code=503, detail="real-trading real-owner authentication is not configured securely")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="real-trading owner login required")
    try:
        claims = decode_access_token(authorization[7:].strip())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired real-trading owner session") from exc
    if claims.get("sub") != OWNER_SUBJECT or claims.get("scope") != OWNER_SCOPE:
        raise HTTPException(status_code=401, detail="Invalid real-trading owner session")
    return OWNER_NAMESPACE_ID
