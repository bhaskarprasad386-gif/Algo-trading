"""Read-only market-feature and score evaluation routes for Early Impulse Pro."""
from __future__ import annotations
from typing import List
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.scanner.early_impulse_engine import evaluate_early_impulse, evaluate_market_features
from app.scanner.early_impulse_signal_store import EarlyImpulseSignalStore

router = APIRouter(prefix="/api/v1/early-impulse", tags=["early-impulse"])
signal_store = EarlyImpulseSignalStore()


class EngineScores(BaseModel):
    absorption: float = Field(default=0, ge=0, le=1)
    synthetic_spot: float = Field(default=0, ge=0, le=1)
    failed_auction: float = Field(default=0, ge=0, le=1)
    market_impact: float = Field(default=0, ge=0, le=1)
    cross_instrument: float = Field(default=0, ge=0, le=1)


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
    return {
        "strategy": "EARLY_IMPULSE_PRO", "status": "WAITING_FOR_FEED_INTEGRATION",
        "feed_connected": False, "scanner_active": False,
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
        "notes": ["Live shared-feed consumer and scanner runner remain disabled until isolated ownership-safe subscription wiring is completed.",
                  "No live signals fabricated; order routing is disabled."],
    }


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
