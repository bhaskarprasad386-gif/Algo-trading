"""Read-only market-feature and score evaluation routes for Early Impulse Pro."""
from __future__ import annotations
from typing import List
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.scanner.early_impulse_engine import evaluate_early_impulse, evaluate_market_features
from app.scanner.early_impulse_signal_store import EarlyImpulseSignalStore
from app.scanner.early_impulse_shoonya_observer import early_impulse_observer

router = APIRouter(prefix="/api/v1/early-impulse", tags=["early-impulse"])
signal_store = early_impulse_observer.runner.store


class EngineScores(BaseModel):
    absorption: float = Field(default=0, ge=0, le=1)
    synthetic_spot: float = Field(default=0, ge=0, le=1)
    failed_auction: float = Field(default=0, ge=0, le=1)
    market_impact: float = Field(default=0, ge=0, le=1)
    cross_instrument: float = Field(default=0, ge=0, le=1)


class RunnerConfig(BaseModel):
    roles: dict[str, str] = Field(default_factory=dict, max_length=5)


class EvaluationRequest(BaseModel):
    direction: str = "NEUTRAL"
    engines: EngineScores = Field(default_factory=EngineScores)
    data_valid: bool = False
    spread_ok: bool = False
    entry_not_extended: bool = False
    invalidation_defined: bool = False
    no_strong_opposite: bool = False


class MarketFeatureRequest(BaseModel):
    prices: List[float] = Field(default_factory=list, max_length=120)
    volumes: List[float] = Field(default_factory=list, max_length=120)
    current_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    quote_age_seconds: float | None = None
    feed_authenticated: bool = False
    future_prices: List[float] = Field(default_factory=list, max_length=120)
    spot_prices: List[float] = Field(default_factory=list, max_length=120)
    cross_prices: List[float] = Field(default_factory=list, max_length=120)
    reference_high: float | None = None
    reference_low: float | None = None


@router.get("/status")
def status() -> dict:
    history = signal_store.snapshot()
    feed = early_impulse_observer.snapshot()
    return {
        "strategy": "EARLY_IMPULSE_PRO", "status": "FEED_OBSERVED" if feed["status"] == "LIVE" else "WAITING_FOR_FEED",
        "feed_connected": feed["connected"], "scanner_active": feed["runner"]["enabled"],
        "runner": feed["runner"],
        "feed_status": feed["status"], "feed_epoch": feed["feed_epoch"],
        "fresh_quote_count": feed["fresh_count"], "stale_quote_count": feed["stale_count"],
        "observer_errors": feed["observer_errors"],
        "signal_count": history["signal_count"], "latest_signal": history["signals"][0] if history["signals"] else None,
        "market_regime": "UNKNOWN", "order_routing_enabled": False, "iv_scanner_dependency": False,
        "signal_history_capacity": history["capacity"], "suppressed_duplicate_count": history["suppressed_duplicate_count"],
        "engines": [
            {"id": "absorption", "name": "Absorption Detection", "weight": 30, "status": "WAITING"},
            {"id": "synthetic_spot", "name": "Synthetic Future vs Spot", "weight": 20, "status": "WAITING"},
            {"id": "failed_auction", "name": "Failed Auction / Rejection", "weight": 15, "status": "WAITING"},
            {"id": "market_impact", "name": "Market Impact Efficiency", "weight": 20, "status": "WAITING"},
            {"id": "cross_instrument", "name": "Cross-Instrument Confirmation", "weight": 15, "status": "WAITING"},
        ],
        "notes": ["Shared observer and synchronized runner are connected. Instrument roles must be configured; subscription ownership remains external to this scanner.",
                  "No live signals fabricated; order routing is disabled."],
    }



@router.post("/runner/config")
def configure_runner(request: RunnerConfig) -> dict:
    try:
        return early_impulse_observer.runner.configure_roles(request.roles)
    except (TypeError, ValueError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/runner/start")
def start_runner() -> dict:
    try:
        return early_impulse_observer.runner.enable()
    except (TypeError, ValueError, RuntimeError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runner/stop")
def stop_runner() -> dict:
    return early_impulse_observer.runner.disable()


@router.get("/runner")
def runner_status() -> dict:
    return early_impulse_observer.runner.snapshot()


@router.get("/signals")
def signals() -> dict:
    """Return bounded scanner-only history; never returns broker order records."""
    return signal_store.snapshot()


@router.post("/evaluate")
def evaluate(request: EvaluationRequest) -> dict:
    """Score normalized engine values; no live feed or broker side effects."""
    return evaluate_early_impulse(request.model_dump())


@router.post("/evaluate-market")
def evaluate_market(request: MarketFeatureRequest) -> dict:
    """Calculate feature scores from caller-supplied observations only."""
    return evaluate_market_features(request.model_dump())
