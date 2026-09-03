from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.routes import (
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    _create_reset_token,
    confirm_password_reset,
    request_password_reset,
)
from app.core.database import Base
from app.core.security import get_password_hash, verify_password
from app.models import PasswordResetToken, User


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(db, email="reset@example.com"):
    user = User(email=email, hashed_password=get_password_hash("old-pass-123"), is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_reset_token_is_hashed_and_single_use():
    db = _db()
    user = _user(db)

    raw = _create_reset_token(db, user)
    db.commit()
    row = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).one()

    assert raw
    assert row.token_hash != raw
    assert row.expires_at > datetime.utcnow()
    assert row.used_at is None

    result = confirm_password_reset(
        PasswordResetConfirmRequest(token=raw, new_password="new-pass-456"), db
    )
    assert result["status"] == "password_reset"
    assert verify_password("new-pass-456", user.hashed_password)

    try:
        confirm_password_reset(
            PasswordResetConfirmRequest(token=raw, new_password="another-pass-789"), db
        )
        assert False, "a reset token must not be reusable"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_expired_token_is_rejected():
    db = _db()
    user = _user(db, "expired@example.com")
    raw = _create_reset_token(db, user, datetime.utcnow() - timedelta(minutes=16))
    db.commit()

    try:
        confirm_password_reset(
            PasswordResetConfirmRequest(token=raw, new_password="new-pass-456"), db
        )
        assert False, "expired reset token must be rejected"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_request_response_does_not_reveal_account_existence():
    db = _db()
    user = _user(db, "known@example.com")

    known = request_password_reset(PasswordResetRequest(identifier=user.email), db)
    unknown = request_password_reset(PasswordResetRequest(identifier="missing@example.com"), db)

    assert known == unknown
    assert "token" not in known
    assert db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).count() == 1


def test_short_new_password_is_rejected():
    db = _db()
    user = _user(db, "short@example.com")
    raw = _create_reset_token(db, user)
    db.commit()

    try:
        confirm_password_reset(
            PasswordResetConfirmRequest(token=raw, new_password="short"), db
        )
        assert False, "password policy must be enforced during reset"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
