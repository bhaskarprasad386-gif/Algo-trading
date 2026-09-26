import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from jose import JWTError, jwt

from app.core.database import get_db
from app.core.security import ALGORITHM, create_access_token, get_password_hash, verify_password
from app.models import PasswordResetToken, Session as UserSession, TradingAccount, User

router = APIRouter(prefix="/api/v1/auth", tags=["User Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

PAPER_STARTING_BALANCE = 10_000_000.0
MOBILE_RE = re.compile(r"^\+?[1-9]\d{9,14}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
PASSWORD_RESET_TTL_MINUTES = 15
GENERIC_RESET_MESSAGE = "If the account exists, reset instructions will be sent to the registered contact."


class RegisterRequest(BaseModel):
    email: str | None = None
    mobile_number: str | None = None
    password: str
    full_name: str | None = None


class LoginRequest(BaseModel):
    identifier: str
    password: str


class PasswordResetRequest(BaseModel):
    identifier: str


class MobileNumberUpdateRequest(BaseModel):
    mobile_number: str
    current_password: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email.strip()))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_mobile(mobile: str) -> str:
    value = mobile.strip().replace(" ", "").replace("-", "")
    if value.startswith("00"):
        value = "+" + value[2:]
    if not MOBILE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Valid mobile number is required")
    return value


def _placeholder_email(mobile: str) -> str:
    return f"mobile-{mobile.lstrip('+')}@accounts.local"


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_token(db: DBSession, user: User, *, device_info: str | None = None) -> TokenResponse:
    token = create_access_token({"sub": str(user.id), "email": user.email})
    db.add(UserSession(user_id=user.id, token_hash=_hash_session_token(token)))
    db.commit()
    return TokenResponse(access_token=token)


def _token_user_id(token: str) -> int:
    from app.core.config import settings
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", "0"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="Invalid authenticated user")
    return user_id


def _require_active_token(db: DBSession, token: str) -> int:
    user_id = _token_user_id(token)
    if db.query(UserSession.id).filter(UserSession.token_hash == _hash_session_token(token)).first() is None:
        raise HTTPException(status_code=401, detail="Token has been logged out or is not an active session")
    return user_id


def _ensure_account(db: DBSession, user: User) -> TradingAccount:
    account = db.query(TradingAccount).filter(TradingAccount.user_id == user.id).first()
    if account:
        return account
    account = TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=PAPER_STARTING_BALANCE, realized_pnl=0.0)
    db.add(account)
    db.flush()
    return account


def _find_user(db: DBSession, identifier: str) -> User | None:
    value = identifier.strip()
    if not value:
        return None
    if "@" in value:
        return db.query(User).filter(User.email == value.lower()).first()
    try:
        mobile = _normalize_mobile(value)
    except HTTPException:
        return None
    return db.query(User).filter(User.mobile_number == mobile).first()


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_reset_token(db: DBSession, user: User, now: datetime | None = None) -> str:
    now = now or _utc_now()
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).update({PasswordResetToken.used_at: now}, synchronize_session=False)
    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token_hash=_hash_reset_token(raw_token), expires_at=now + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES), created_at=now))
    return raw_token


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DBSession = Depends(get_db)):
    email = payload.email.strip().lower() if payload.email else None
    mobile = _normalize_mobile(payload.mobile_number) if payload.mobile_number else None
    if not email and not mobile:
        raise HTTPException(status_code=400, detail="Email or mobile number is required")
    if email and not _valid_email(email):
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if email and db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    if mobile and db.query(User).filter(User.mobile_number == mobile).first():
        raise HTTPException(status_code=409, detail="Mobile number already registered")
    stored_email = email or _placeholder_email(mobile)
    user = User(email=stored_email, mobile_number=mobile, hashed_password=get_password_hash(payload.password), full_name=payload.full_name.strip() if payload.full_name else None)
    db.add(user)
    try:
        db.flush()
        _ensure_account(db, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email or mobile number already registered") from exc
    db.refresh(user)
    return _issue_token(db, user, device_info="web")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DBSession = Depends(get_db)):
    identifier = payload.identifier.strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Email or mobile number is required")
    if "@" in identifier:
        lookup = identifier.lower()
        user = db.query(User).filter(User.email == lookup).first()
    else:
        mobile = _normalize_mobile(identifier)
        user = db.query(User).filter(User.mobile_number == mobile).first()
    if not user or not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email/mobile or password")
    _ensure_account(db, user)
    db.commit()
    return _issue_token(db, user, device_info="web")


@router.post("/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, db: DBSession = Depends(get_db)):
    user = _find_user(db, payload.identifier)
    if user and user.is_active:
        _create_reset_token(db, user)
        db.commit()
    return {"status": "accepted", "message": GENERIC_RESET_MESSAGE}


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirmRequest, db: DBSession = Depends(get_db)):
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    token_hash = _hash_reset_token(payload.token.strip())
    now = _utc_now()
    reset = db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == token_hash).first()
    if not reset or reset.used_at is not None or reset.expires_at <= now:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user = db.query(User).filter(User.id == reset.user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user.hashed_password = get_password_hash(payload.new_password)
    reset.used_at = now
    db.commit()
    return {"status": "password_reset", "message": "Password reset successfully. Please login again."}


@router.get("/me")
def me(token: str = Depends(oauth2_scheme), db: DBSession = Depends(get_db)):
    user_id = _require_active_token(db, token)
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    account = _ensure_account(db, user)
    db.commit()
    return {"id": user.id, "email": None if user.email.endswith("@accounts.local") else user.email, "mobile_number": user.mobile_number, "full_name": user.full_name, "account": {"id": account.id, "mode": account.mode, "virtual_balance": account.virtual_balance, "realized_pnl": account.realized_pnl, "is_active": account.is_active}}


@router.patch("/me/mobile")
def update_mobile_number(payload: MobileNumberUpdateRequest, token: str = Depends(oauth2_scheme), db: DBSession = Depends(get_db)):
    user_id = _require_active_token(db, token)
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    mobile = _normalize_mobile(payload.mobile_number)
    existing = db.query(User).filter(User.mobile_number == mobile, User.id != user_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Mobile number is already registered")
    user.mobile_number = mobile
    db.commit()
    return {"status": "updated", "mobile_number": user.mobile_number}


@router.post("/logout")
def logout(token: str = Depends(oauth2_scheme), db: DBSession = Depends(get_db)):
    _token_user_id(token)
    db.query(UserSession).filter(UserSession.token_hash == _hash_session_token(token)).delete(synchronize_session=False)
    db.commit()
    return {"status": "logged_out"}
