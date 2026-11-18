"""Shared identity lookup for user-scoped API routes, without paper-account side effects."""
from __future__ import annotations

from threading import RLock

from fastapi import Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import User

_user_bootstrap_lock = RLock()


def current_user_id(db: Session = Depends(get_db)) -> int:
    """Return an active user ID, creating only the system identity if needed."""
    with _user_bootstrap_lock:
        user = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).first()
        if user is None:
            try:
                with db.begin_nested():
                    user = User(
                        email="system@local.algo-trading",
                        hashed_password="",
                        full_name="Algo Trading System",
                        is_active=True,
                    )
                    db.add(user)
                    db.flush()
            except IntegrityError:
                user = db.query(User).filter(User.email == "system@local.algo-trading").first()
            if user is None or not user.is_active:
                raise HTTPException(status_code=503, detail="user identity bootstrap unavailable")
            db.commit()
            db.refresh(user)
        return int(user.id)
