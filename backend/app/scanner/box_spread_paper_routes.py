"""Dedicated Box Spread paper-trading API. Never submits broker orders."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import math
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
import hmac

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token
from sqlalchemy import func, or_, select

from app.core.database import SessionLocal
from app.market_data.instruments import InstrumentMaster
from app.market_data.box_spread_subscriptions import select_box_contracts
from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS
from app.scanner.live_box_spread_routes import live as live_box_spread_opportunities
from app.scanner.box_spread_contract_resolver import (
    BoxSpreadContractResolutionError, resolve_box_spread_contracts,
)
from app.scanner.box_spread_paper_engine import BoxSpreadPaperEngine, BoxSpreadPaperEngineError
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperLedger, BoxSpreadPaperTradeRecord, BoxSpreadPaperLedgerEntry,
)

router = APIRouter(prefix="/api/v1/scanner/box-spread-paper", tags=["box-spread-paper"])
_engine = BoxSpreadPaperEngine()
_master = InstrumentMaster()
_ledger = BoxSpreadPaperLedger(SessionLocal)
# Explicit per-process operator switch. Always resets to OFF on API restart.
_paper_execution_enabled = False
_paper_execution_state_lock = threading.Lock()
# Per-trade entry locks prevent same-process concurrent entry requests.
_entry_locks: dict[tuple[str, int], asyncio.Lock] = {}

# Best-effort per-process brute-force protection. Deliberately use the socket peer
# address only; X-Forwarded-For is untrusted unless proxy trust is configured.
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_BUCKET_LIMIT = 4096
_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()


def _paper_login_client_host(request: Request | None) -> str:
    """Resolve the login rate-limit key without trusting spoofable proxy headers.

    Forwarded addresses are considered only when the direct socket peer belongs
    to BOX_SPREAD_PAPER_TRUSTED_PROXY_CIDRS. Walk X-Forwarded-For right-to-left
    so a trusted proxy-appended client address wins over user-supplied prefixes.
    """
    peer = request.client.host if request is not None and request.client is not None else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    networks = []
    for item in settings.BOX_SPREAD_PAPER_TRUSTED_PROXY_CIDRS.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            # Invalid trust entries never broaden trust; the peer remains the key.
            continue
    if not networks or not any(peer_ip in network for network in networks):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "") if request is not None else ""
    if not forwarded:
        return peer
    try:
        forwarded_ips = [ipaddress.ip_address(part.strip()) for part in forwarded.split(",")]
    except ValueError:
        # Malformed forwarding chains are ignored rather than partially trusted.
        return peer

    for candidate in reversed(forwarded_ips):
        if any(candidate in network for network in networks):
            continue
        return str(candidate)
    return peer


def _consume_paper_login_attempt(client_host: str) -> None:
    now = time.monotonic()
    with _login_attempts_lock:
        bucket = [stamp for stamp in _login_attempts.get(client_host, [])
                  if now - stamp < _LOGIN_WINDOW_SECONDS]
        if len(bucket) >= _LOGIN_MAX_ATTEMPTS:
            _login_attempts[client_host] = bucket
            retry_after = max(1, int(_LOGIN_WINDOW_SECONDS - (now - bucket[0])) + 1)
            raise HTTPException(
                status_code=429,
                detail="Too many paper-trading login attempts; try again later",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        _login_attempts[client_host] = bucket
        if len(_login_attempts) > _LOGIN_BUCKET_LIMIT:
            # Bound memory under high-cardinality source-address traffic.
            expired = [key for key, stamps in _login_attempts.items()
                       if not stamps or now - stamps[-1] >= _LOGIN_WINDOW_SECONDS]
            for key in expired:
                _login_attempts.pop(key, None)
            while len(_login_attempts) > _LOGIN_BUCKET_LIMIT:
                _login_attempts.pop(next(iter(_login_attempts)))


def _clear_paper_login_attempts(client_host: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(client_host, None)




class PaperLogin(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@router.post("/auth/login")
def paper_login(payload: PaperLogin, request: Request = None):
    """Issue a short-lived token for the single configured paper-trading owner."""
    configured_password = settings.BOX_SPREAD_PAPER_PASSWORD
    if not configured_password:
        raise HTTPException(status_code=503, detail="Box Spread paper access is not configured on the server")
    if len(configured_password) < 16:
        raise HTTPException(status_code=503, detail="Box Spread paper password must be at least 16 characters")
    if len(settings.SECRET_KEY) < 32:
        raise HTTPException(status_code=503, detail="A strong server SECRET_KEY is required for paper-trading sessions")
    client_host = _paper_login_client_host(request)
    _consume_paper_login_attempt(client_host)
    if not hmac.compare_digest(payload.password.encode("utf-8"), configured_password.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid paper-trading password")
    _clear_paper_login_attempts(client_host)
    token = create_access_token({"sub": "box-spread-paper-owner", "scope": "box-spread-paper"}, expires_delta=timedelta(minutes=60))
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}


def require_paper_access(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Fail closed unless a valid single-owner bearer token is supplied."""
    if not settings.BOX_SPREAD_PAPER_PASSWORD:
        raise HTTPException(status_code=503, detail="Box Spread paper access is not configured on the server")
    if len(settings.BOX_SPREAD_PAPER_PASSWORD) < 16:
        raise HTTPException(status_code=503, detail="Box Spread paper password must be at least 16 characters")
    if len(settings.SECRET_KEY) < 32:
        raise HTTPException(status_code=503, detail="A strong server SECRET_KEY is required for paper-trading sessions")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Paper-trading login required")
    try:
        claims = decode_access_token(authorization[7:].strip())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired paper-trading session") from exc
    if claims.get("sub") != "box-spread-paper-owner" or claims.get("scope") != "box-spread-paper":
        raise HTTPException(status_code=401, detail="Invalid paper-trading session")
    return claims


class PaperExecutionControl(BaseModel):
    enabled: bool


class CreatePaperTrade(BaseModel):
    underlying: str = Field(min_length=1, max_length=64)
    instrument_class: str
    expiry: str
    low_strike: float = Field(gt=0)
    high_strike: float = Field(gt=0)
    direction: str
    lots: int = Field(ge=1, le=100)
    idempotency_key: str = Field(min_length=1, max_length=100)
    account_key: str = Field(default="default", min_length=1, max_length=128)
    entry_spot_price: float | None = Field(default=None, gt=0)


class SimulateEntry(BaseModel):
    # Browser reference prices are never trusted; the server reloads a fresh scanner snapshot.
    reference_prices: dict[str, float] | None = None
    account_key: str = Field(default="default", min_length=1, max_length=128)
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    limit_prices: dict[str, float] | None = None


class ExpirySettlement(BaseModel):
    settlement_date: date
    settlement_prices: dict[str, float]
    account_key: str = Field(default="default", min_length=1, max_length=128)


class ExitLeg(BaseModel):
    role: str
    quantity: int = Field(ge=1)
    # Compatibility-only: execution price is always derived from a fresh backend quote.
    fill_price: float = Field(default=0, ge=0)
    fill_id: str = Field(min_length=1, max_length=96)
    account_key: str = Field(default="default", min_length=1, max_length=128)


class ExitBasket(BaseModel):
    fill_id: str = Field(min_length=1, max_length=96)
    account_key: str = Field(default="default", min_length=1, max_length=128)
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    # Optional per-leg limits. When omitted, LIMIT defaults to the current executable quote.
    limit_prices: dict[str, float] | None = None


def _resolve(payload: CreatePaperTrade):
    if not _master.instruments:
        _master.download()
    cls = payload.instrument_class.strip().upper()
    allowed_stocks = frozenset(str(x).upper() for x in TOP15_LIQUID_STOCK_SYMBOLS)
    selection = select_box_contracts(
        _master,
        underlying=payload.underlying,
        instrument_class=cls,
        atm_strike=(payload.low_strike + payload.high_strike) / 2,
        expiry=payload.expiry,
        allowed_stock_symbols=allowed_stocks,
    )
    return resolve_box_spread_contracts(
        subscriptions=selection.subscriptions,
        underlying=payload.underlying,
        instrument_class=cls,
        expiry=payload.expiry,
        low_strike=payload.low_strike,
        high_strike=payload.high_strike,
        direction=payload.direction,
    )


def _error(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (ValueError, LookupError, BoxSpreadContractResolutionError, BoxSpreadPaperEngineError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=503, detail="Instrument master or paper ledger is temporarily unavailable") from exc


def _canonical_expiry(value: object) -> str:
    text = str(value or "").strip().upper()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d%b%Y", "%d%b%y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _reference_prices_for_direction(row: dict[str, Any], direction: str) -> dict[str, float]:
    """Validate complete two-sided quotes and select executable prices before claiming entry."""
    quote_pairs = {
        "LOW_CE": ("low_call_bid", "low_call_ask"),
        "HIGH_CE": ("high_call_bid", "high_call_ask"),
        "HIGH_PE": ("high_put_bid", "high_put_ask"),
        "LOW_PE": ("low_put_bid", "low_put_ask"),
    }
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    prices: dict[str, float] = {}
    for role, (bid_field, ask_field) in quote_pairs.items():
        try:
            bid, ask = float(row[bid_field]), float(row[ask_field])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise HTTPException(
                status_code=409,
                detail=f"fresh executable entry quote is unavailable for {role}",
            ) from None
        if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= 0 or bid > ask:
            raise HTTPException(
                status_code=409,
                detail=f"fresh executable entry quote is unavailable for {role}",
            )
        buy = direction == "LONG" if role in {"LOW_CE", "HIGH_PE"} else direction == "SHORT"
        prices[role] = ask if buy else bid
    return prices


def _exit_reference_price(row: dict[str, Any], direction: str, role: str) -> float:
    """Validate the leg's two-sided market and use the executable side to close it."""
    quote_pairs = {
        "LOW_CE": ("low_call_bid", "low_call_ask"),
        "HIGH_CE": ("high_call_bid", "high_call_ask"),
        "HIGH_PE": ("high_put_bid", "high_put_ask"),
        "LOW_PE": ("low_put_bid", "low_put_ask"),
    }
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if role not in quote_pairs:
        raise HTTPException(status_code=422, detail="unknown paper leg role")
    bid_field, ask_field = quote_pairs[role]
    try:
        bid, ask = float(row[bid_field]), float(row[ask_field])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=409, detail="fresh executable exit quote is unavailable") from None
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= 0 or bid > ask:
        raise HTTPException(status_code=409, detail="fresh executable exit quote is unavailable")
    # Closing a long position sells at bid; closing a short position buys at ask.
    sell_to_close = (role in {"LOW_CE", "HIGH_PE"}) == (direction == "LONG")
    return bid if sell_to_close else ask


def _fresh_mark_prices_for_trade(
    trade_id: int,
    account_key: str,
    *,
    trade_record: BoxSpreadPaperTradeRecord | None = None,
    live_snapshot: dict[str, Any] | None = None,
) -> dict[str, float] | None:
    """Return executable close-side marks from one shared fresh backend snapshot."""
    if trade_record is None:
        request = _load_trade_request(trade_id, account_key)
    else:
        if trade_record.id != trade_id or trade_record.account_key != account_key:
            raise ValueError("paper trade not found for account")
        request = CreatePaperTrade(
            underlying=trade_record.underlying,
            instrument_class=trade_record.instrument_class,
            expiry=trade_record.expiry,
            low_strike=float(trade_record.low_strike),
            high_strike=float(trade_record.high_strike),
            direction=trade_record.direction,
            lots=trade_record.lots,
            account_key=account_key,
            idempotency_key=f"portfolio-mark-{trade_id}",
            entry_spot_price=float(trade_record.entry_spot_price) if trade_record.entry_spot_price is not None else None,
        )
    contracts = _resolve(request)
    row = _matching_live_row(contracts, snapshot=live_snapshot)
    if row is None:
        return None
    return {
        role: _exit_reference_price(row, contracts.direction, role)
        for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
    }


def _strike_matches(raw_value: Any, expected: float) -> bool:
    """Treat malformed scanner strike data as a non-match, never as a route error."""
    try:
        value = float(raw_value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(value) and abs(value - expected) < 0.0001


def _matching_live_row(
    contracts,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if snapshot is None:
        snapshot = live_box_spread_opportunities(limit=500, max_age_seconds=5.0)
    return next((
        row for row in snapshot.get("data", [])
        if str(row.get("symbol", "")).strip().upper() == contracts.underlying
        and str(row.get("instrument_class", "")).strip().upper() == contracts.instrument_class
        and _canonical_expiry(row.get("expiry_date") or row.get("expiry")) == _canonical_expiry(contracts.expiry)
        and _strike_matches(row.get("low_strike"), contracts.low_strike)
        and _strike_matches(row.get("high_strike"), contracts.high_strike)
        and str(row.get("direction", "")).strip().upper() == contracts.direction
    ), None)


def _engine_key(*, trade_id: int, account_key: str, idempotency_key: str) -> str:
    """Namespace in-memory engine keys by persistent trade and paper account."""
    account_digest = hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:16]
    key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"paper:{trade_id}:{account_digest}:{key_digest}"


def _ledger_fill_id(idempotency_key: str, role: str) -> str:
    """Keep durable per-leg fill IDs stable and within the ledger's 128-char limit."""
    key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"paper:{key_digest}:{role}"


def _contract_payload(contracts) -> list[dict[str, Any]]:
    return [{
        "role": leg.role, "exchange_type": leg.exchange_type, "token": leg.token,
        "symbol": leg.symbol, "underlying": leg.underlying, "expiry": leg.expiry,
        "option_type": leg.option_type, "strike": leg.strike, "lot_size": leg.lot_size,
    } for leg in contracts.legs]


@router.get("/execution-control", dependencies=[Depends(require_paper_access)])
def get_execution_control():
    with _paper_execution_state_lock:
        enabled = _paper_execution_enabled
    return {"mode": "PAPER_ONLY", "broker_orders_enabled": False, "enabled": enabled,
            "status": "ON" if enabled else "OFF", "restart_default": "OFF"}


@router.post("/execution-control", dependencies=[Depends(require_paper_access)])
def set_execution_control(payload: PaperExecutionControl):
    global _paper_execution_enabled
    with _paper_execution_state_lock:
        _paper_execution_enabled = payload.enabled
        enabled = _paper_execution_enabled
    return {"mode": "PAPER_ONLY", "broker_orders_enabled": False, "enabled": enabled,
            "status": "ON" if enabled else "OFF", "restart_default": "OFF",
            "message": "New paper entries enabled" if enabled else "New paper entries disabled; existing positions and safe exits remain available"}


@router.get("/account", dependencies=[Depends(require_paper_access)])
def account(account_key: str = "default"):
    try:
        balance = _ledger.ensure_account(account_key)
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False,
                "account_key": account_key, "virtual_balance": str(balance)}
    except Exception as exc:
        _error(exc)


@router.get("/portfolio-summary", dependencies=[Depends(require_paper_access)])
def portfolio_summary(account_key: str = "default"):
    """Account-wide paper P&L, independent of any positions page or page size."""
    try:
        with SessionLocal() as session:
            trade_rows = session.scalars(
                select(BoxSpreadPaperTradeRecord)
                .where(BoxSpreadPaperTradeRecord.account_key == account_key)
                .order_by(BoxSpreadPaperTradeRecord.created_at, BoxSpreadPaperTradeRecord.id)
            ).all()
            if not trade_rows:
                return {
                    "mode": "PAPER_ONLY", "broker_orders_enabled": False,
                    "account_key": account_key, "trade_count": 0,
                    "open_position_count": 0, "closed_position_count": 0,
                    "realized_pnl": "0.00", "unrealized_pnl": "0.00",
                    "net_pnl": "0.00", "total_charges": "0.00",
                    "unrealized_status": "FRESH_OR_NO_OPEN_POSITIONS",
                }
            trade_by_id = {row.id: row for row in trade_rows}
            all_entries = session.scalars(
                select(BoxSpreadPaperLedgerEntry)
                .where(BoxSpreadPaperLedgerEntry.account_key == account_key)
                .where(BoxSpreadPaperLedgerEntry.trade_id.in_(list(trade_by_id)))
                .order_by(BoxSpreadPaperLedgerEntry.created_at, BoxSpreadPaperLedgerEntry.id)
            ).all()
            entries_by_trade: dict[int, list[BoxSpreadPaperLedgerEntry]] = {
                trade_id: [] for trade_id in trade_by_id
            }
            for entry in all_entries:
                entries_by_trade[entry.trade_id].append(entry)

        open_rows = []
        closed_count = 0
        for row in trade_rows:
            entries = entries_by_trade[row.id]
            signed_by_role: dict[str, int] = {}
            for entry in entries:
                signed_by_role[entry.role] = signed_by_role.get(entry.role, 0) + (
                    entry.quantity if entry.action == "BUY" else -entry.quantity
                )
            has_exposure = any(quantity != 0 for quantity in signed_by_role.values())
            if row.status == "CLOSED" or (
                row.status not in {"CANCELLED"} and entries and not has_exposure
            ):
                closed_count += 1
            elif row.status not in {"CLOSED", "CANCELLED"}:
                open_rows.append(row)

        mark_snapshot = None
        mark_snapshot_loaded = False
        realized_total = Decimal("0.00")
        charges_total = Decimal("0.00")
        unrealized_total = Decimal("0.00")
        unrealized_available = True
        for row in trade_rows:
            entries = entries_by_trade[row.id]
            signed_by_role: dict[str, int] = {}
            for entry in entries:
                signed_by_role[entry.role] = signed_by_role.get(entry.role, 0) + (
                    entry.quantity if entry.action == "BUY" else -entry.quantity
                )
            has_exposure = any(quantity != 0 for quantity in signed_by_role.values())
            if row.status in {"CLOSED", "CANCELLED"} or (entries and not has_exposure):
                marks = None
            elif not entries:
                marks = None
                unrealized_available = False
            else:
                try:
                    if not mark_snapshot_loaded:
                        mark_snapshot = live_box_spread_opportunities(limit=500, max_age_seconds=5.0)
                        mark_snapshot_loaded = True
                    marks = _fresh_mark_prices_for_trade(
                        row.id, account_key, trade_record=row,
                        live_snapshot=mark_snapshot,
                    )
                except (HTTPException, ValueError, LookupError, BoxSpreadContractResolutionError):
                    mark_snapshot = {"data": []}
                    mark_snapshot_loaded = True
                    marks = None
                if marks is None:
                    unrealized_available = False
            summary = _ledger.get_trade_summary(
                row.id, account_key=account_key, mark_prices=marks,
                trade_record=row, entries=entries,
            )
            realized_total += Decimal(str(summary.get("realized_pnl") or 0))
            charges_total += Decimal(str(summary.get("total_charges") or 0))
            if row in open_rows:
                if summary.get("unrealized_pnl") is None:
                    unrealized_available = False
                else:
                    unrealized_total += Decimal(str(summary["unrealized_pnl"]))

        unrealized_value = unrealized_total.quantize(Decimal("0.01")) if unrealized_available else None
        net_value = (realized_total + unrealized_value).quantize(Decimal("0.01")) if unrealized_value is not None else None
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "account_key": account_key, "trade_count": len(trade_rows),
            "open_position_count": len(open_rows), "closed_position_count": closed_count,
            "realized_pnl": str(realized_total.quantize(Decimal("0.01"))),
            "unrealized_pnl": str(unrealized_value) if unrealized_value is not None else None,
            "net_pnl": str(net_value) if net_value is not None else None,
            "total_charges": str(charges_total.quantize(Decimal("0.01"))),
            "unrealized_status": "FRESH_OR_NO_OPEN_POSITIONS" if unrealized_available else "UNAVAILABLE_STALE_OR_MISSING_QUOTES",
        }
    except Exception as exc:
        _error(exc)


@router.get("/trades", dependencies=[Depends(require_paper_access)])
def trades(
    account_key: str = "default",
    limit: int = 100,
    offset: int = 0,
    include_ledger: bool = False,
    position_state: str = "ALL",
    search: str = "",
    direction: str = "ALL",
):
    position_state = str(position_state or "ALL").strip().upper()
    direction = str(direction or "ALL").strip().upper()
    search = str(search or "").strip().lstrip("#").strip()
    if position_state not in {"ALL", "OPEN", "CLOSED"}:
        raise HTTPException(status_code=422, detail="position_state must be ALL, OPEN or CLOSED")
    if direction not in {"ALL", "LONG", "SHORT"}:
        raise HTTPException(status_code=422, detail="direction must be ALL, LONG or SHORT")
    if len(search) > 128:
        raise HTTPException(status_code=422, detail="search must be at most 128 characters")
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    if not 0 <= offset <= 1_000_000:
        raise HTTPException(status_code=422, detail="offset must be between 0 and 1000000")
    try:
        with SessionLocal() as session:
            trade_filters = [BoxSpreadPaperTradeRecord.account_key == account_key]
            if direction != "ALL":
                trade_filters.append(BoxSpreadPaperTradeRecord.direction == direction)
            if search:
                search_clause = func.lower(BoxSpreadPaperTradeRecord.underlying).contains(search.lower())
                if search.isdigit():
                    search_clause = or_(search_clause, BoxSpreadPaperTradeRecord.id == int(search))
                trade_filters.append(search_clause)
            if position_state != "ALL":
                # Classify from durable signed quantities, not trade status alone:
                # partial exits can leave exposure after some exit fills.
                state_rows = session.execute(
                    select(BoxSpreadPaperTradeRecord.id, BoxSpreadPaperTradeRecord.status)
                    .where(*trade_filters)
                ).all()
                state_entries = session.scalars(
                    select(BoxSpreadPaperLedgerEntry)
                    .where(BoxSpreadPaperLedgerEntry.account_key == account_key)
                ).all()
                by_trade: dict[int, list[BoxSpreadPaperLedgerEntry]] = {}
                for entry in state_entries:
                    by_trade.setdefault(entry.trade_id, []).append(entry)
                matching_ids = []
                for trade_id, status in state_rows:
                    entries_for_trade = by_trade.get(trade_id, [])
                    signed: dict[str, int] = {}
                    for entry in entries_for_trade:
                        signed[entry.role] = signed.get(entry.role, 0) + (
                            entry.quantity if entry.action == "BUY" else -entry.quantity
                        )
                    has_exposure = any(quantity != 0 for quantity in signed.values())
                    is_closed = status == "CLOSED" or (
                        status != "CANCELLED" and bool(entries_for_trade) and not has_exposure
                    )
                    is_open = status not in {"CLOSED", "CANCELLED"} and (
                        not entries_for_trade or has_exposure
                    )
                    if (position_state == "OPEN" and is_open) or (position_state == "CLOSED" and is_closed):
                        matching_ids.append(trade_id)
                trade_filters.append(BoxSpreadPaperTradeRecord.id.in_(matching_ids))
            total_count = session.scalar(
                select(func.count(BoxSpreadPaperTradeRecord.id)).where(*trade_filters)
            ) or 0
            rows = session.scalars(select(BoxSpreadPaperTradeRecord)
                .where(*trade_filters)
                .order_by(BoxSpreadPaperTradeRecord.created_at.desc(), BoxSpreadPaperTradeRecord.id.desc())
                .offset(offset)
                .limit(limit)).all()
            data = []
            ledger_entries_by_trade: dict[int, list[BoxSpreadPaperLedgerEntry]] = {
                row.id: [] for row in rows
            }
            if include_ledger and rows:
                # Load all fills in one bounded query instead of issuing a query
                # for every trade in the portfolio.
                fill_rows = session.scalars(
                    select(BoxSpreadPaperLedgerEntry)
                    .where(
                        BoxSpreadPaperLedgerEntry.account_key == account_key,
                        BoxSpreadPaperLedgerEntry.trade_id.in_(list(ledger_entries_by_trade)),
                    )
                    .order_by(
                        BoxSpreadPaperLedgerEntry.trade_id,
                        BoxSpreadPaperLedgerEntry.created_at,
                        BoxSpreadPaperLedgerEntry.id,
                    )
                ).all()
                for fill in fill_rows:
                    ledger_entries_by_trade[fill.trade_id].append(fill)

            mark_snapshot: dict[str, Any] | None = None
            mark_snapshot_loaded = False
            for row in rows:
                item = {
                    "trade_id": row.id, "underlying": row.underlying,
                    "instrument_class": row.instrument_class, "expiry": row.expiry,
                    "low_strike": float(row.low_strike), "high_strike": float(row.high_strike),
                    "direction": row.direction, "lots": row.lots, "quantity": row.quantity,
                    "lot_size": row.lot_size, "status": row.status,
                    "created_at": row.created_at.isoformat(),
                    "entry_spot_price": float(row.entry_spot_price) if row.entry_spot_price is not None else None,
                    "fee_schedule_version": row.fee_schedule_version,
                }
                if include_ledger:
                    entries = ledger_entries_by_trade[row.id]
                    entry_rows = [entry for entry in entries if not entry.fill_id.startswith(("EXIT:", "SETTLE:"))]
                    exit_rows = [entry for entry in entries if entry.fill_id.startswith(("EXIT:", "SETTLE:"))]
                    mark_prices = None
                    mark_status = "UNAVAILABLE_STALE_OR_MISSING_QUOTES"
                    if entries and row.status not in {"CLOSED", "CANCELLED"}:
                        try:
                            if not mark_snapshot_loaded:
                                mark_snapshot = live_box_spread_opportunities(limit=500, max_age_seconds=5.0)
                                mark_snapshot_loaded = True
                            mark_prices = _fresh_mark_prices_for_trade(
                                row.id,
                                account_key,
                                trade_record=row,
                                live_snapshot=mark_snapshot,
                            )
                            if mark_prices is not None:
                                mark_status = "FRESH_BACKEND_QUOTES"
                        except (HTTPException, ValueError, LookupError, BoxSpreadContractResolutionError):
                            # A failed or stale snapshot must never become an estimated MTM.
                            mark_snapshot = {"data": []}
                            mark_snapshot_loaded = True
                            mark_prices = None
                    summary = _ledger.get_trade_summary(
                        row.id,
                        account_key=account_key,
                        mark_prices=mark_prices,
                        trade_record=row,
                        entries=entries,
                    )
                    legs = []
                    for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"):
                        role_entries = [entry for entry in entries if entry.role == role]
                        role_entry_rows = [entry for entry in role_entries if not entry.fill_id.startswith(("EXIT:", "SETTLE:"))]
                        role_exit_rows = [entry for entry in role_entries if entry.fill_id.startswith(("EXIT:", "SETTLE:"))]
                        entry_quantity = sum(entry.quantity for entry in role_entry_rows)
                        signed_open = sum(entry.quantity * (1 if entry.action == "BUY" else -1) for entry in role_entries)
                        weighted_entry = sum((Decimal(entry.fill_price) * entry.quantity for entry in role_entry_rows), Decimal("0"))
                        average_entry = weighted_entry / entry_quantity if entry_quantity else None
                        exit_quantity = sum(entry.quantity for entry in role_exit_rows)
                        weighted_exit = sum((Decimal(entry.fill_price) * entry.quantity for entry in role_exit_rows), Decimal("0"))
                        legs.append({
                            "role": role,
                            "entry_action": role_entry_rows[0].action if role_entry_rows else None,
                            "entry_quantity": entry_quantity,
                            "entry_average_price": str(average_entry) if average_entry is not None else None,
                            "open_quantity": abs(signed_open),
                            "open_action": "BUY" if signed_open > 0 else "SELL" if signed_open < 0 else "FLAT",
                            "mark_price": str(mark_prices[role]) if mark_prices and role in mark_prices else None,
                            "exit_quantity": exit_quantity,
                            "exit_average_price": str(weighted_exit / exit_quantity) if exit_quantity else None,
                        })
                    data_entries = [{
                        "fill_id": entry.fill_id, "role": entry.role, "action": entry.action,
                        "quantity": entry.quantity, "fill_price": str(entry.fill_price),
                        "notional": str(entry.notional), "charges": str(entry.charges),
                        "cash_delta": str(entry.cash_delta), "created_at": entry.created_at.isoformat(),
                    } for entry in entries]
                    item.update({
                        "entry_at": min((entry.created_at for entry in entry_rows), default=None).isoformat() if entry_rows else None,
                        "closed_at": max((entry.created_at for entry in exit_rows), default=None).isoformat() if exit_rows and summary.get("status") == "CLOSED" else None,
                        "mark_status": mark_status,
                        "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
                        "legs": legs,
                        "entries": data_entries,
                    })
                data.append(item)
            return {
                "mode": "PAPER_ONLY",
                "broker_orders_enabled": False,
                "count": len(data),
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(data) < total_count,
                "position_state": position_state,
                "data": data,
            }
    except Exception as exc:
        _error(exc)


@router.post("/trades", dependencies=[Depends(require_paper_access)])
def create_trade(payload: CreatePaperTrade):
    try:
        contracts = _resolve(payload)
        trade = _ledger.create_trade(
            idempotency_key=payload.idempotency_key, underlying=contracts.underlying,
            instrument_class=contracts.instrument_class, expiry=contracts.expiry,
            low_strike=contracts.low_strike, high_strike=contracts.high_strike,
            direction=contracts.direction, lots=payload.lots, lot_size=contracts.lot_size,
            account_key=payload.account_key, entry_spot_price=payload.entry_spot_price,
        )
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "trade_id": trade.id, "status": trade.status,
            "idempotency_key": trade.idempotency_key,
            "contracts": _contract_payload(contracts),
            "next_step": "Run paper simulation against fresh backend Box Spread quotes; no broker order is sent.",
        }
    except Exception as exc:
        _error(exc)


def _load_trade_request(trade_id: int, account_key: str) -> CreatePaperTrade:
    with SessionLocal() as session:
        trade = session.get(BoxSpreadPaperTradeRecord, trade_id)
        if trade is None or trade.account_key != account_key:
            raise HTTPException(status_code=404, detail="paper trade not found")
        return CreatePaperTrade(
            underlying=trade.underlying, instrument_class=trade.instrument_class,
            expiry=trade.expiry, low_strike=float(trade.low_strike),
            high_strike=float(trade.high_strike), direction=trade.direction,
            lots=trade.lots, idempotency_key=trade.idempotency_key,
            account_key=account_key,
        )


@router.post("/trades/{trade_id}/simulate-entry", dependencies=[Depends(require_paper_access)])
async def simulate_entry(trade_id: int, payload: SimulateEntry):
    # Serialize entry attempts for the same paper trade within this API process.
    # The durable-fill check below remains the restart/replay protection.
    lock_key = (payload.account_key, trade_id)
    entry_lock = _entry_locks.setdefault(lock_key, asyncio.Lock())
    async with entry_lock:
        return await _simulate_entry_locked(trade_id, payload)


async def _simulate_entry_locked(trade_id: int, payload: SimulateEntry):
    try:
        with _paper_execution_state_lock:
            enabled = _paper_execution_enabled
        if not enabled:
            raise HTTPException(status_code=423, detail="Live paper trading is OFF. Manually switch execution ON before starting a new paper entry.")
        request = await asyncio.to_thread(_load_trade_request, trade_id, payload.account_key)
        existing_claim = await asyncio.to_thread(
            _ledger.get_entry_claim, trade_id=trade_id, account_key=payload.account_key
        )
        if existing_claim is not None:
            raise HTTPException(
                status_code=409,
                detail="Paper entry attempt is already durably claimed; automatic retry is blocked. Inspect recovery and the ledger before any manual decision.",
            )
        # A process restart clears the in-memory execution registry. Never replay
        # a trade with durable fills against a newer quote snapshot: that could
        # conflict with the ledger's stable fill IDs or mix two entry snapshots.
        existing_fills = await asyncio.to_thread(
            _ledger.list_fills, trade_id, account_key=payload.account_key
        )
        if existing_fills:
            raise HTTPException(
                status_code=409,
                detail="Paper entry already has durable fills; duplicate execution was blocked. Inspect the ledger before any recovery action.",
            )
        # Instrument-master refresh, scanner snapshot reads, and SQL writes are
        # synchronous dependencies; keep them off the ASGI event loop.
        contracts = await asyncio.to_thread(_resolve, request)
        live_snapshot = await asyncio.to_thread(
            live_box_spread_opportunities, limit=500, max_age_seconds=5.0
        )
        current_row = next((
            row for row in live_snapshot.get("data", [])
            if str(row.get("symbol", "")).strip().upper() == contracts.underlying
            and str(row.get("instrument_class", "")).strip().upper() == contracts.instrument_class
            and _canonical_expiry(row.get("expiry_date") or row.get("expiry")) == _canonical_expiry(contracts.expiry)
            and _strike_matches(row.get("low_strike"), contracts.low_strike)
            and _strike_matches(row.get("high_strike"), contracts.high_strike)
            and str(row.get("direction", "")).strip().upper() == contracts.direction
        ), None)
        if current_row is None:
            raise HTTPException(status_code=409, detail="No matching fresh live Box Spread opportunity; paper execution was not started")
        # Never trust prices posted by the browser. Use the current backend scanner
        # row, which has already passed the five-second four-leg freshness check.
        # Map executable bid/ask sides by each paper leg action. Never feed a
        # sell-side quote into a simulated BUY (or vice versa).
        reference_prices = _reference_prices_for_direction(current_row, contracts.direction)
        limit_prices = None
        if payload.order_type == "LIMIT":
            limit_prices = payload.limit_prices or dict(reference_prices)
            if set(limit_prices) != {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}:
                raise HTTPException(status_code=422, detail="LIMIT entry requires exactly four leg limit prices")
            actions = (
                {"LOW_CE": "BUY", "HIGH_CE": "SELL", "HIGH_PE": "BUY", "LOW_PE": "SELL"}
                if contracts.direction == "LONG"
                else {"LOW_CE": "SELL", "HIGH_CE": "BUY", "HIGH_PE": "SELL", "LOW_PE": "BUY"}
            )
            for role, raw_limit in limit_prices.items():
                try:
                    limit = float(raw_limit)
                except (TypeError, ValueError, OverflowError):
                    raise HTTPException(status_code=422, detail=f"{role} limit price must be a positive finite number") from None
                if not math.isfinite(limit) or limit <= 0:
                    raise HTTPException(status_code=422, detail=f"{role} limit price must be a positive finite number")
                executable = reference_prices[role]
                if actions[role] == "BUY" and limit < executable:
                    raise HTTPException(status_code=409, detail=f"{role} BUY limit is below current ask; all four entry legs rejected")
                if actions[role] == "SELL" and limit > executable:
                    raise HTTPException(status_code=409, detail=f"{role} SELL limit is above current bid; all four entry legs rejected")
        elif payload.limit_prices is not None:
            raise HTTPException(status_code=422, detail="MARKET entry must not include limit_prices")
        # Recheck immediately before the durable claim: OFF pressed while quotes
        # and contracts were being prepared must prevent this entry from starting.
        # Once the claim is created, the four-leg attempt is in flight; OFF does
        # not cancel it midway because that could strand a partial four-leg state.
        with _paper_execution_state_lock:
            enabled = _paper_execution_enabled
        if not enabled:
            raise HTTPException(
                status_code=423,
                detail="Live paper trading was switched OFF before entry started; no entry claim was created.",
            )
        # Durable unique claim serializes entry across separate API worker processes.
        # It remains after a crash: ambiguous execution is inspected manually, never retried automatically.
        claim = await asyncio.to_thread(
            _ledger.claim_entry_attempt, trade_id=trade_id, account_key=payload.account_key
        )
        if not claim.get("created"):
            raise HTTPException(
                status_code=409,
                detail="Paper entry attempt is already durably claimed; automatic retry is blocked. Inspect recovery and the ledger before any manual decision.",
            )
        engine_key = _engine_key(
            trade_id=trade_id, account_key=payload.account_key,
            idempotency_key=request.idempotency_key,
        )
        await _engine.create_intent(
            contracts=contracts, lots=request.lots, idempotency_key=engine_key,
        )
        execution = await _engine.execute_intent(
            idempotency_key=engine_key, reference_prices=reference_prices,
            limit_prices=limit_prices,
        )
        basket_legs = {
            fill.role: {
                "fill_id": _ledger_fill_id(request.idempotency_key, fill.role),
                "action": fill.action, "quantity": fill.quantity,
                "fill_price": fill.fill_price,
            }
            for fill in execution.fills
        }
        receipts = await asyncio.to_thread(
            _ledger.record_entry_basket,
            trade_id=trade_id, legs=basket_legs, account_key=payload.account_key,
        )
        summary = await asyncio.to_thread(
            _ledger.get_trade_summary, trade_id, account_key=payload.account_key
        )
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "trade_id": trade_id, "status": summary["status"],
            "latency_seconds": execution.latency_seconds,
            "slippage_rate": str(execution.slippage_rate),
            "fills": [{
                "role": fill.role, "action": fill.action, "quantity": fill.quantity,
                "reference_price": str(fill.reference_price), "fill_price": str(fill.fill_price),
                "slippage": str(fill.slippage), "notional": str(fill.notional),
                "charges": str(receipt.charges), "virtual_balance": str(receipt.virtual_balance),
            } for fill, receipt in zip(execution.fills, receipts, strict=True)],
            "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
        }
    except HTTPException:
        raise
    except Exception as exc:
        _error(exc)


@router.post("/trades/{trade_id}/exit", dependencies=[Depends(require_paper_access)])
def exit_leg(trade_id: int, payload: ExitLeg):
    try:
        # Never trust browser-entered exit prices. Resolve this persisted trade
        # and require its matching fresh four-leg scanner row, just like entry.
        request = _load_trade_request(trade_id, payload.account_key)
        contracts = _resolve(request)
        current_row = _matching_live_row(contracts)
        if current_row is None:
            raise HTTPException(
                status_code=409,
                detail="No matching fresh live Box Spread opportunity; paper exit was not recorded",
            )
        executable_price = _exit_reference_price(
            current_row, contracts.direction, payload.role,
        )
        receipt = _ledger.record_exit_fill(
            trade_id=trade_id, fill_id=payload.fill_id, role=payload.role,
            quantity=payload.quantity, fill_price=executable_price,
            account_key=payload.account_key,
        )
        summary = _ledger.get_trade_summary(trade_id, account_key=payload.account_key)
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "receipt": {
                "trade_id": receipt.trade_id, "fill_id": receipt.fill_id,
                "role": receipt.role, "action": receipt.action,
                "quantity": receipt.quantity, "fill_price": str(receipt.fill_price),
                "charges": str(receipt.charges), "cash_delta": str(receipt.cash_delta),
                "virtual_balance": str(receipt.virtual_balance),
            },
            "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
        }
    except Exception as exc:
        _error(exc)


def _paper_open_basket_state(trade_id: int, account_key: str) -> tuple[dict[str, Any], dict[str, int]]:
    rows = _ledger.list_open_trades(account_key=account_key)
    trade_row = next((item for item in rows if int(item["trade_id"]) == trade_id), None)
    if trade_row is None:
        raise HTTPException(status_code=404, detail="no open paper position found for this trade")
    quantities = {
        str(leg["role"]): int(leg["open_position_quantity"])
        for leg in trade_row.get("legs", [])
    }
    roles = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
    if set(quantities) != set(roles) or any(quantities.get(role, 0) <= 0 for role in roles):
        raise HTTPException(status_code=409, detail="four open legs are required for a basket exit")
    if len({quantities[role] for role in roles}) != 1:
        raise HTTPException(status_code=409, detail="four-leg open quantities are unequal; basket exit is blocked")
    return trade_row, quantities


def _exit_quote_fields(role: str) -> tuple[str, str]:
    fields = {
        "LOW_CE": ("low_call_bid", "low_call_ask"),
        "HIGH_CE": ("high_call_bid", "high_call_ask"),
        "HIGH_PE": ("high_put_bid", "high_put_ask"),
        "LOW_PE": ("low_put_bid", "low_put_ask"),
    }
    if role not in fields:
        raise HTTPException(status_code=422, detail="unknown paper leg role")
    return fields[role]


@router.get("/trades/{trade_id}/exit-preview", dependencies=[Depends(require_paper_access)])
def preview_exit_basket(trade_id: int, account_key: str = "default"):
    """Read-only four-leg quote and P&L preview; it never records a fill."""
    try:
        request = _load_trade_request(trade_id, account_key)
        contracts = _resolve(request)
        row = _matching_live_row(contracts)
        if row is None:
            raise HTTPException(status_code=409, detail="matching fresh four-leg quotes are unavailable")
        trade_row, quantities = _paper_open_basket_state(trade_id, account_key)
        preview_legs = []
        marks: dict[str, float] = {}
        gross_cash_flow = Decimal("0.00")
        for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"):
            bid_field, ask_field = _exit_quote_fields(role)
            try:
                bid, ask = float(row[bid_field]), float(row[ask_field])
            except (KeyError, TypeError, ValueError, OverflowError):
                raise HTTPException(status_code=409, detail=f"fresh bid/ask unavailable for {role}") from None
            if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= 0 or bid > ask:
                raise HTTPException(status_code=409, detail=f"valid executable bid/ask unavailable for {role}")
            leg = next(item for item in trade_row["legs"] if item["role"] == role)
            close_action = "SELL" if leg["open_position_action"] == "BUY" else "BUY"
            executable = bid if close_action == "SELL" else ask
            marks[role] = executable
            gross_cash_flow += Decimal(str(executable)) * quantities[role] * (1 if close_action == "SELL" else -1)
            preview_legs.append({
                "role": role, "close_action": close_action,
                "quantity": quantities[role], "bid": bid, "ask": ask,
                "default_limit_price": executable, "executable_price": executable,
            })
        summary = _ledger.get_trade_summary(
            trade_id, account_key=account_key, mark_prices=marks,
        )
        estimated_net_pnl = Decimal(summary["realized_pnl"]) + Decimal(summary["unrealized_pnl"] or 0)
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "order_type_default": "LIMIT", "trade_id": trade_id,
            "quantity_per_leg": next(iter(quantities.values())),
            "legs": preview_legs, "gross_close_cash_flow": str(gross_cash_flow),
            "estimated_charges_included": True,
            "estimated_realized_plus_close_pnl": str(estimated_net_pnl),
            "quote_source": "fresh_backend_four_leg_snapshot",
        }
    except Exception as exc:
        _error(exc)


@router.post("/trades/{trade_id}/exit-basket", dependencies=[Depends(require_paper_access)])
def exit_basket(trade_id: int, payload: ExitBasket):
    """Paper-only all-four-leg exit; any non-marketable limit rejects the whole basket."""
    try:
        request = _load_trade_request(trade_id, payload.account_key)
        contracts = _resolve(request)
        row = _matching_live_row(contracts)
        if row is None:
            raise HTTPException(status_code=409, detail="matching fresh four-leg quotes are unavailable; no exit recorded")
        trade_row, quantities = _paper_open_basket_state(trade_id, payload.account_key)
        if payload.limit_prices is not None and set(payload.limit_prices) != {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}:
            raise HTTPException(status_code=422, detail="limit_prices must contain exactly all four leg roles")

        basket_legs: dict[str, dict[str, object]] = {}
        for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"):
            bid_field, ask_field = _exit_quote_fields(role)
            try:
                bid, ask = float(row[bid_field]), float(row[ask_field])
            except (KeyError, TypeError, ValueError, OverflowError):
                raise HTTPException(status_code=409, detail=f"fresh bid/ask unavailable for {role}") from None
            if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= 0 or bid > ask:
                raise HTTPException(status_code=409, detail=f"valid executable bid/ask unavailable for {role}")
            leg = next(item for item in trade_row["legs"] if item["role"] == role)
            close_action = "SELL" if leg["open_position_action"] == "BUY" else "BUY"
            executable = bid if close_action == "SELL" else ask
            if payload.order_type == "LIMIT":
                limit_price = executable if payload.limit_prices is None else float(payload.limit_prices[role])
                if not math.isfinite(limit_price) or limit_price <= 0:
                    raise HTTPException(status_code=422, detail=f"valid positive limit price required for {role}")
                # A paper limit is considered immediately executable only when it
                # crosses the current quote. Otherwise the entire basket stays open.
                if close_action == "SELL" and limit_price > bid:
                    raise HTTPException(status_code=409, detail=f"{role} sell limit is not executable at the current bid; no legs were exited")
                if close_action == "BUY" and limit_price < ask:
                    raise HTTPException(status_code=409, detail=f"{role} buy limit is not executable at the current ask; no legs were exited")
            basket_legs[role] = {"quantity": quantities[role], "fill_price": executable}

        receipts = _ledger.record_exit_basket(
            trade_id=trade_id, fill_id=payload.fill_id,
            legs=basket_legs, account_key=payload.account_key,
        )
        summary = _ledger.get_trade_summary(trade_id, account_key=payload.account_key)
        return {
            "mode": "PAPER_ONLY", "broker_orders_enabled": False,
            "order_type": payload.order_type, "trade_id": trade_id,
            "status": summary["status"],
            "receipts": [{
                "fill_id": receipt.fill_id, "role": receipt.role,
                "action": receipt.action, "quantity": receipt.quantity,
                "fill_price": str(receipt.fill_price),
                "charges": str(receipt.charges), "cash_delta": str(receipt.cash_delta),
            } for receipt in receipts],
            "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
        }
    except Exception as exc:
        _error(exc)


@router.post("/trades/{trade_id}/settle-expiry", dependencies=[Depends(require_paper_access)])
def settle_expiry(trade_id: int, payload: ExpirySettlement):
    # Fail closed: caller-supplied leg prices are not authenticated official settlement data.
    # Re-enable only after a trusted server-side underlying settlement source is verified
    # and CE/PE intrinsic values are derived from persisted strikes and option types.
    raise HTTPException(
        status_code=503,
        detail="Expiry settlement is temporarily disabled until official settlement data can be verified server-side.",
    )


@router.get("/trades/{trade_id}/ledger", dependencies=[Depends(require_paper_access)])
def trade_ledger(trade_id: int, account_key: str = "default"):
    try:
        # MTM is optional: if any quote is missing, invalid, or older than the
        # scanner's five-second limit, return unrealized_pnl=null rather than
        # marking against stale or browser-supplied prices.
        mark_prices = None
        mark_status = "UNAVAILABLE_STALE_OR_MISSING_QUOTES"
        try:
            mark_prices = _fresh_mark_prices_for_trade(trade_id, account_key)
            if mark_prices is not None:
                mark_status = "FRESH_BACKEND_QUOTES"
        except (HTTPException, ValueError, LookupError, BoxSpreadContractResolutionError):
            mark_prices = None
        summary = _ledger.get_trade_summary(
            trade_id, account_key=account_key, mark_prices=mark_prices,
        )
        with SessionLocal() as session:
            rows = session.scalars(select(BoxSpreadPaperLedgerEntry)
                .where(BoxSpreadPaperLedgerEntry.trade_id == trade_id,
                       BoxSpreadPaperLedgerEntry.account_key == account_key)
                .order_by(BoxSpreadPaperLedgerEntry.created_at, BoxSpreadPaperLedgerEntry.id)).all()
            entries = [{
                "fill_id": row.fill_id, "role": row.role, "action": row.action,
                "quantity": row.quantity, "fill_price": str(row.fill_price),
                "notional": str(row.notional), "charges": str(row.charges),
                "cash_delta": str(row.cash_delta), "created_at": row.created_at.isoformat(),
            } for row in rows]
        return {"mode": "PAPER_ONLY", "broker_orders_enabled": False,
                "summary": {key: str(value) if isinstance(value, Decimal) else value for key, value in summary.items()},
                "mark_status": mark_status,
                "entries": entries}
    except Exception as exc:
        _error(exc)


@router.get("/recovery", dependencies=[Depends(require_paper_access)])
def recovery(account_key: str = "default"):
    """List durable non-terminal paper trades after restart; never replay fills automatically."""
    try:
        open_trades = _ledger.list_open_trades(account_key=account_key)
        return {
            "mode": "PAPER_ONLY",
            "broker_orders_enabled": False,
            "recovery_mode": "READ_ONLY",
            "automatic_replay": False,
            "count": len(open_trades),
            "data": open_trades,
        }
    except Exception as exc:
        _error(exc)


__all__ = ["router"]