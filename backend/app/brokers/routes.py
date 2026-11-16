from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.brokers.angel_one import AngelOneAdapter
from app.brokers.shoonya_routes import router as shoonya_router
from app.brokers.zerodha_routes import router as zerodha_router
from app.brokers.fyers_routes import router as fyers_router
from app.brokers.connections import broker_connections
from app.brokers.registry import BrokerRegistry
from app.brokers.capabilities import broker_capabilities
from app.brokers.safety import trading_safety
from app.brokers.session_store import broker_sessions
from app.brokers.real_owner_auth import RealOwnerLogin, login_real_owner, require_real_owner_access
from app.brokers.real_owner_state import claim_authenticated_account, disable_owner_trading, set_owner_connection_state
from app.core.database import get_db
from app.core.logger import app_logger
from app.execution.recovery_coordinator import review_account_recovery
from app.execution.broker_snapshot_event_sync import sync_account_order_status_events
from app.models import BoxSpreadRealOwnerState

router = APIRouter(prefix="/api/v1/brokers", tags=["brokers"])
router.include_router(shoonya_router)
router.include_router(zerodha_router)
router.include_router(fyers_router)


class ConnectRequest(BaseModel):
    broker: str = Field(min_length=2, max_length=50)
    display_name: str | None = Field(default=None, max_length=100)
    api_key: str = Field(min_length=1, max_length=300)
    client_code: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=300)
    totp_secret: str = Field(min_length=1, max_length=300)


class RealTradingEnableRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=100)



@router.post("/owner-auth/login")
def owner_login(payload: RealOwnerLogin, request: Request):
    """Issue a short-lived token for the separately configured Box Spread owner."""
    return login_real_owner(payload, request)


@router.get("")
def supported_brokers(user_id: int = Depends(require_real_owner_access)) -> dict:
    return {"brokers": BrokerRegistry().names()}



@router.get("/capabilities")
def broker_capability_matrix(user_id: int = Depends(require_real_owner_access)) -> dict:
    """Return explicit provider coverage without granting execution capability."""
    return {
        "brokers": [
            {
                "broker": item.broker,
                "display_name": item.display_name,
                "history_provider": item.history_provider,
                "history_window": item.history_window,
                "history_limitations": item.history_limitations,
                "connection_flow": item.connection_flow,
                "adapter_registered_for_routing": item.adapter_registered_for_routing,
                "can_place_orders": item.can_place_orders,
                "can_cancel_orders": item.can_cancel_orders,
                "can_modify_orders": item.can_modify_orders,
                "live_certified": item.live_certified,
            }
            for item in broker_capabilities.all()
        ],
        "execution_authorized": False,
    }


@router.get("/connections")
def connections(user_id: int = Depends(require_real_owner_access)) -> dict:
    return {"connections": [{"broker": c.broker, "connected": c.connected, "display_name": c.display_name, "connected_at": c.connected_at.isoformat() if c.connected_at else None} for c in broker_connections.list(user_id)]}




@router.post("/events/sync")
def sync_broker_order_status_events(
    db: Session = Depends(get_db),
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Persist immutable order-status evidence from one complete current-day broker snapshot."""
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
            "status": "BLOCKED",
            "reason": "BROKER_SESSION_NOT_CONNECTED",
            "event_count": 0,
            "created_count": 0,
            "trading_authorized": False,
        }
    account_id = str(getattr(adapter, "account_id", "") or "").strip()
    if not account_id:
        return {
            "status": "BLOCKED",
            "reason": "BROKER_SESSION_ACCOUNT_ID_UNAVAILABLE",
            "event_count": 0,
            "created_count": 0,
            "trading_authorized": False,
        }
    try:
        return sync_account_order_status_events(
            db, adapter=adapter, account_id=account_id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail="Broker order snapshot is invalid") from exc
    except Exception as exc:
        db.rollback()
        app_logger.exception("Broker order-status snapshot sync failed")
        raise HTTPException(status_code=503, detail="Broker order-status synchronization is temporarily unavailable") from exc


@router.get("/recovery/review")
def review_execution_recovery(
    db: Session = Depends(get_db),
    user_id: int = Depends(require_real_owner_access),
) -> dict:
    """Read-only review of pending execution intents against the active broker session."""
    owner_state = db.query(BoxSpreadRealOwnerState).filter(
        BoxSpreadRealOwnerState.owner_key == "box-spread-real-owner"
    ).first()
    if owner_state is None or not owner_state.broker_name:
        raise HTTPException(status_code=409, detail="No authenticated Box Spread broker account is bound")

    broker = str(owner_state.broker_name).strip().lower()
    adapter = broker_sessions.get(user_id, broker)
    # Close the owner-state read transaction before the recovery inventory opens
    # its own bounded transaction.
    db.rollback()
    if adapter is None or not bool(getattr(adapter, "connected", False)):
        return {
            "status": "BLOCKED",
            "reason": "BROKER_SESSION_NOT_CONNECTED",
            "trading_authorized": False,
            "candidate_count": 0,
            "reviews": [],
        }
    account_id = str(getattr(adapter, "account_id", "") or "").strip()
    if not account_id:
        return {
            "status": "BLOCKED",
            "reason": "BROKER_SESSION_ACCOUNT_ID_UNAVAILABLE",
            "trading_authorized": False,
            "candidate_count": 0,
            "reviews": [],
        }
    try:
        return review_account_recovery(
            db, account_id=account_id, broker=broker, adapter=adapter,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail="Recovery review request is invalid") from exc
    except Exception as exc:
        db.rollback()
        app_logger.exception("Read-only execution recovery review failed")
        raise HTTPException(status_code=503, detail="Execution recovery review is temporarily unavailable") from exc


@router.get("/safety")
def safety_status(db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    # Report the durable owner flags, not process-local memory that resets on restart.
    state = db.query(BoxSpreadRealOwnerState).filter(
        BoxSpreadRealOwnerState.owner_key == "box-spread-real-owner"
    ).first()
    if state is None:
        return {"real_trading_enabled": False, "kill_switch": True, "enabled_at": None}
    return {
        "real_trading_enabled": state.real_trading_enabled,
        "kill_switch": state.kill_switch,
        "enabled_at": None,
    }


@router.post("/safety/enable")
def enable_real_trading(payload: RealTradingEnableRequest, user_id: int = Depends(require_real_owner_access)) -> dict:
    if payload.confirmation.strip().upper() != "ENABLE REAL TRADING":
        raise HTTPException(status_code=400, detail="Explicit confirmation required: ENABLE REAL TRADING")

    # Fail closed until durable single-account ownership, session lifecycle,
    # order reconciliation, risk controls, and recovery are implemented.
    raise HTTPException(
        status_code=503,
        detail="Real-trading arming is unavailable until durable account ownership and execution safety controls are implemented",
    )


@router.post("/safety/disable")
def disable_real_trading(db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    # Always disarm process-local state first, even if durable storage is unavailable.
    trading_safety.disable(user_id)
    try:
        state = disable_owner_trading(db)
    except Exception as exc:
        db.rollback()
        app_logger.error("Durable Box Spread disable failed: %s", exc)
        raise HTTPException(status_code=503, detail="Trading is disabled locally but durable kill-switch state could not be confirmed") from exc
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "message": "Real trading disabled and kill switch persisted."}


@router.post("/safety/kill-switch")
def emergency_kill_switch(db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    # Engage the local kill switch before attempting the durable write.
    trading_safety.disable(user_id)
    try:
        state = disable_owner_trading(db)
    except Exception as exc:
        db.rollback()
        app_logger.error("Durable Box Spread kill-switch update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Kill switch is engaged locally but durable state could not be confirmed") from exc
    return {"real_trading_enabled": state.real_trading_enabled, "kill_switch": state.kill_switch, "message": "Emergency kill switch engaged and persisted."}


@router.post("/connect")
def connect(payload: ConnectRequest, db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    broker = payload.broker.strip().lower()
    if broker != "angel_one":
        raise HTTPException(status_code=400, detail=f"Unsupported broker: {broker}")

    # A reconnect attempt must never inherit an armed process-local safety state.
    trading_safety.disable(user_id)
    adapter = AngelOneAdapter()
    try:
        result = adapter.connect(api_key=payload.api_key, client_code=payload.client_code, password=payload.password, totp_secret=payload.totp_secret, account_id=payload.client_code)
    except Exception as exc:
        app_logger.error("Angel One connection failed: %s", exc)
        raise HTTPException(status_code=502, detail="Angel One connection failed") from exc
    try:
        claim_authenticated_account(db, broker, result.get("client_code") or "")
    except HTTPException:
        adapter.disconnect()
        raise
    except Exception as exc:
        adapter.disconnect()
        db.rollback()
        app_logger.error("Durable Box Spread account binding failed: %s", exc)
        raise HTTPException(status_code=503, detail="Durable Box Spread account ownership is unavailable") from exc
    try:
        set_owner_connection_state(db, True)
    except Exception as exc:
        adapter.disconnect()
        db.rollback()
        app_logger.error("Durable Box Spread connection state update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Durable Box Spread connection state is unavailable") from exc
    logout_error = broker_sessions.replace_and_disconnect_previous(user_id, broker, adapter)
    if logout_error is not None:
        app_logger.error("Previous %s provider logout failed during reconnect: %s", broker, type(logout_error).__name__)
    item = broker_connections.connect(user_id, broker, payload.display_name)
    trading_safety.disable(user_id)
    return {"connected": item.connected, "broker": item.broker, "display_name": item.display_name, "client_code": result.get("client_code"), "real_trading": False}


@router.get("/{broker}/status")
def status(broker: str, db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    key = (user_id, broker.strip().lower())
    adapter = broker_sessions.get(*key)
    item = broker_connections.get(user_id, key[1])
    owner_state = db.query(BoxSpreadRealOwnerState).filter(
        BoxSpreadRealOwnerState.owner_key == "box-spread-real-owner"
    ).first()
    # The durable row is authoritative; missing ownership must remain fail-closed.
    return {
        "broker": key[1],
        "connected": bool(adapter and adapter.connected and item and item.connected),
        "display_name": item.display_name if item else None,
        "real_trading": owner_state.real_trading_enabled if owner_state else False,
        "kill_switch": owner_state.kill_switch if owner_state else True,
    }


@router.delete("/{broker}")
def disconnect(broker: str, db: Session = Depends(get_db), user_id: int = Depends(require_real_owner_access)) -> dict:
    name = broker.strip().lower()
    # Remove local reachability and disarm first. Provider logout is best-effort:
    # even if it raises, metadata and durable state must still be reconciled.
    adapter = broker_sessions.remove(user_id, name)
    trading_safety.disable(user_id)
    broker_connections.disconnect(user_id, name)

    disconnect_error = None
    if adapter is not None:
        try:
            adapter.disconnect()
        except Exception as exc:
            disconnect_error = exc
            app_logger.exception("Broker provider disconnect failed for %s", name)

    try:
        set_owner_connection_state(db, False)
    except Exception as exc:
        db.rollback()
        app_logger.error("Durable Box Spread disconnect state update failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Broker session was removed locally and trading remains disarmed, but durable connection state could not be confirmed",
        ) from exc

    if disconnect_error is not None:
        raise HTTPException(
            status_code=503,
            detail="Broker session was removed locally and trading remains disarmed, but provider logout failed; verify the session with the broker",
        ) from disconnect_error
    return {"connected": False, "broker": name, "real_trading": False, "kill_switch": True}


@router.get("/web/settings", include_in_schema=False)
def broker_settings_page() -> HTMLResponse:
    page = Path(__file__).resolve().parents[3] / "web" / "dashboard" / "broker.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Broker settings page unavailable")
    return HTMLResponse(content=page.read_text(encoding="utf-8"), media_type="text/html")


# Keep the m.Stock Type A login flow separate from the Angel One /connect contract.
from app.brokers.mstock_routes import router as mstock_router

router.include_router(mstock_router)
