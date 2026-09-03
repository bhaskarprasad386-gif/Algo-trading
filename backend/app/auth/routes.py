import re

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models import TradingAccount, User

router = APIRouter(prefix="/api/v1/auth", tags=["User Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

PAPER_STARTING_BALANCE = 1_000_000.0
MOBILE_RE = re.compile(r"^\+?[1-9]\d{9,14}$")


class RegisterRequest(BaseModel):
    email: str | None = None
    mobile_number: str | None = None
    password: str
    full_name: str | None = None


class LoginRequest(BaseModel):
    identifier: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _valid_email(email: str) -> bool:
    value = email.strip().lower()
    return "@" in value and "." in value.rsplit("@", 1)[-1]


def _normalize_mobile(mobile: str) -> str:
    value = mobile.strip().replace(" ", "").replace("-", "")
    if value.startswith("00"):
        value = "+" + value[2:]
    if not MOBILE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Valid mobile number is required")
    return value


def _placeholder_email(mobile: str) -> str:
    return f"mobile-{mobile.lstrip('+')}@accounts.local"


def _issue_token(user: User) -> TokenResponse:
    token = create_access_token({"sub": str(user.id), "email": user.email})
    return TokenResponse(access_token=token)


def _ensure_account(db: Session, user: User) -> TradingAccount:
    account = db.query(TradingAccount).filter(TradingAccount.user_id == user.id).first()
    if account:
        return account
    account = TradingAccount(user_id=user.id, mode="PAPER", virtual_balance=PAPER_STARTING_BALANCE)
    db.add(account)
    db.flush()
    return account


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
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
    user = User(
        email=stored_email,
        mobile_number=mobile,
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name.strip() if payload.full_name else None,
    )
    db.add(user)
    db.flush()
    _ensure_account(db, user)
    db.commit()
    db.refresh(user)
    return _issue_token(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
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
    return _issue_token(user)


@router.get("/me")
def me(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    from jose import JWTError, jwt
    from app.core.security import ALGORITHM
    from app.core.config import settings

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", "0"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    account = _ensure_account(db, user)
    db.commit()
    return {
        "id": user.id,
        "email": None if user.email.endswith("@accounts.local") else user.email,
        "mobile_number": user.mobile_number,
        "full_name": user.full_name,
        "account": {
            "id": account.id,
            "mode": account.mode,
            "virtual_balance": account.virtual_balance,
            "is_active": account.is_active,
        },
    }


@router.post("/logout")
def logout(token: str = Depends(oauth2_scheme)):
    # JWT access tokens are stateless; clients must discard the token.
    return {"status": "logged_out"}
